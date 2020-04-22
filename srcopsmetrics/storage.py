#!/usr/bin/env python3
# SrcOpsMetrics
# Copyright (C) 2020 Dominik Tuchyna
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""GitHub Knowledge Storage handling."""

import json
import logging
import os
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from thoth.storages.ceph import CephStore
from thoth.storages.exceptions import NotFoundError

from srcopsmetrics import utils
from srcopsmetrics.entity_schema import Schemas
from srcopsmetrics.enums import EntityTypeEnum

_LOGGER = logging.getLogger(__name__)

class ProcessedKnowledge:

    def __init__(self, f):
        self.func = f

    def __call__(self, *args, **kwargs):
        def wrapper():
            return self.func(*args, **kwargs)

        project = os.getenv('PROJECT')

        preprocessed_dir = Path(f'./srcopsmetrics/preprocessed/{project}')
        utils.check_directory(preprocessed_dir)
        total_path = preprocessed_dir.joinpath(f'{self.func.__name__ }.json')
        
        storage = KnowledgeStorage(os.getenv('IS_LOCAL'))

        knowledge = storage.load_previous_knowledge(file_path=total_path, knowledge_type='Processed Knowledge')

        if knowledge is None or knowledge == {}:
            knowledge = wrapper()
            storage.save_knowledge(file_path=total_path, data=knowledge)

        return knowledge

    def __get__(self, instance, owner):
        return partial(self.__call__, instance)


class KnowledgeStorage:
    """Class for knowledge loading and saving."""

    _FILENAME_ENTITY = {
        "Issue": "issues",
        "PullRequest": "pull_requests",
        "ContentFile": "content_file",
    }

    _GITHUB_ACCESS_TOKEN = os.getenv("GITHUB_ACCESS_TOKEN")
    _KEY_ID = os.getenv("CEPH_KEY_ID")
    _SECRET_KEY = os.getenv("CEPH_SECRET_KEY")
    _PREFIX = os.getenv("CEPH_BUCKET_PREFIX")
    _HOST = os.getenv("S3_ENDPOINT_URL")
    _BUCKET = os.getenv("CEPH_BUCKET")

    def __init__(self, is_local: Optional[bool] = False):
        """Initialize to behave as either local or remote storage."""
        self.is_local = is_local

        _LOGGER.debug("Use %s for knowledge loading and storing." %
                      ('local' if is_local else 'Ceph'))

    def save_knowledge(self, file_path: Path, data: Dict[str, Any]):
        """Save collected knowledge as json.

        The saved json contains one dictionary with single key 'results'
        under which the knowledge is stored.

        Arguments:
            file_path {Path} -- where the knowledge should be saved
            data {Dict[str, Any]} -- collected knowledge. Should be json compatible
        """
        results = {"results": data}

        _LOGGER.info("Saving knowledge file %s of size %d" %
                     (os.path.basename(file_path), len(data)))

        if not self.is_local:
            ceph_filename = os.path.relpath(file_path).replace("./", "")
            s3 = self.get_ceph_store()
            s3.store_document(results, ceph_filename)
            _LOGGER.info("Saved on CEPH at %s%s%s" %
                         (s3.bucket, s3.prefix, ceph_filename))
        else:
            with open(file_path, "w") as f:
                json.dump(results, f)
            _LOGGER.info("Saved locally at %s" % file_path)

    def get_ceph_store(self) -> CephStore:
        """Establish a connection to the CEPH."""
        s3 = CephStore(
            key_id=self._KEY_ID,
            secret_key=self._SECRET_KEY,
            prefix=self._PREFIX,
            host=self._HOST,
            bucket=self._BUCKET
        )
        s3.connect()
        return s3

    def load_previous_knowledge(self, project_name: str = None, knowledge_type: str = None, file_path: Optional[Path] = None) -> Dict[str, Any]:
        """Load previously collected repo knowledge. If a repo was not inspected before, create its directory.

        Arguments:
            file_ath {Path} -- path of the inspected github repository

        Returns:
            Dict[str, Any] -- previusly collected knowledge.
                            Empty dict if the knowledge does not exist.

        """

        if file_path is None:
            filename = self._FILENAME_ENTITY[knowledge_type]
            pwd = Path.cwd().joinpath("./srcopsmetrics/bot_knowledge")
            project_path = pwd.joinpath("./" + project_name)
            file_path = project_path.joinpath("./" + filename + ".json")

        results = self.load_locally(
            file_path) if self.is_local else self.load_remotely(file_path)

        if results is None:
            _LOGGER.info("No previous knowledge found for %s" % project_name)
            results = {}
        else:
            _LOGGER.info(
                "Found previous knowledge for %s with %d entities of type %s" % (
                    project_name, len(results), knowledge_type)
            )
        return results

    @staticmethod
    def load_locally(file_path: Path) -> json:
        """Load knowledge file from local storage."""
        _LOGGER.info("Loading knowledge locally")
        if not file_path.exists() or os.path.getsize(file_path) == 0:
            _LOGGER.debug("Knowledge %s not found locally" % file_path)
            return None
        with open(file_path, "r") as f:
            data = json.load(f)
            results = data["results"]
        return results

    def load_remotely(self, file_path: Path) -> json:
        """Load knowledge file from Ceph storage."""
        _LOGGER.info("Loading knowledge from Ceph")
        ceph_filename = os.path.relpath(file_path).replace("./", "")
        try:
            return self.get_ceph_store().retrieve_document(ceph_filename)["results"]
        except NotFoundError:
            _LOGGER.debug("Knowledge %s not found on Ceph" % file_path)

from os.path import join
