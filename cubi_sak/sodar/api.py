"""Client API code for SODAR."""

import io
from types import SimpleNamespace
import typing

from altamisa.isatab import (
    InvestigationInfo,
    Study,
    Assay,
    InvestigationReader,
    StudyReader,
    AssayReader,
)
import attr
import cattr
from logzero import logger
import requests

from ..exceptions import ParameterException, UnsupportedIsaTabFeatureException
from . import models


def _investigations_get(*, sodar_url, sodar_api_token, project_uuid):
    """Get investigation information."""
    while sodar_url.endswith("/"):
        sodar_url = sodar_url[:-1]
    url_tpl = "%(sodar_url)s/samplesheets/api/investigation/retrieve/%(project_uuid)s"
    url = url_tpl % {"sodar_url": sodar_url, "project_uuid": project_uuid}

    logger.debug("HTTP GET request to %s", url)
    headers = {"Authorization": "Token %s" % sodar_api_token}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return cattr.structure(r.json(), models.Investigation)


#: Investigation-related API.
investigations = SimpleNamespace(get=_investigations_get)


def _samplesheets_get(*, sodar_url, sodar_api_token, project_uuid):
    """Get ISA-tab sample sheet from SODAR."""
    while sodar_url.endswith("/"):
        sodar_url = sodar_url[:-1]
    url_tpl = "%(sodar_url)s/samplesheets/api/export/json/%(project_uuid)s"
    url = url_tpl % {"sodar_url": sodar_url, "project_uuid": project_uuid}

    logger.debug("HTTP GET request to %s", url)
    headers = {"Authorization": "Token %s" % sodar_api_token}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


#: Samplesheets-related API.
samplesheets = SimpleNamespace(get=_samplesheets_get)


def _landingzones_list(*, sodar_url, sodar_api_token, project_uuid):
    """Return landing zones in project."""
    while sodar_url.endswith("/"):
        sodar_url = sodar_url[:-1]
    url_tpl = "%(sodar_url)s/landingzones/api/list/%(project_uuid)s"
    url = url_tpl % {"sodar_url": sodar_url, "project_uuid": project_uuid}

    logger.debug("HTTP GET request to %s", url)
    headers = {"Authorization": "Token %s" % sodar_api_token}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return cattr.structure(r.json(), typing.List[models.LandingZone])


def _landingzones_create(*, sodar_url, sodar_api_token, project_uuid, assay_uuid=None):
    """Create landing zone in project."""
    while sodar_url.endswith("/"):
        sodar_url = sodar_url[:-1]

    # Retrieve sample sheet for assay if not given.
    if not assay_uuid:
        investigation = investigations.get(
            sodar_url=sodar_url, sodar_api_token=sodar_api_token, project_uuid=project_uuid
        )
        if len(investigation.studies) != 1:
            logger.error("Expected one study, found %d", len(investigation.studies))
            logger.info("Try specifying an explicit --assay parameter")
            raise Exception("Invalid number of studies in investigation!")
        study = list(investigation.studies.values())[0]
        if len(study.assays) != 1:
            logger.error("Expected one assay, found %d", len(study.assays))
            logger.info("Try specifying an explicit --assay parameter")
            raise Exception("Invalid number of assays in investigation!")
        assay_uuid = list(study.assays.keys())[0]

    # Create landing zone through API.
    url_tpl = "%(sodar_url)s/landingzones/api/create/%(project_uuid)s"
    url = url_tpl % {"sodar_url": sodar_url, "project_uuid": project_uuid}
    logger.debug("HTTP POST request to %s", url)
    headers = {"Authorization": "Token %s" % sodar_api_token}
    data = {"assay": assay_uuid}
    r = requests.post(url, data=data, headers=headers)
    r.raise_for_status()
    return cattr.structure(r.json(), models.LandingZone)


def _landingzones_move(*, sodar_url, sodar_api_token, landing_zone_uuid):
    """Move landing zone with the given UUID."""
    while sodar_url.endswith("/"):
        sodar_url = sodar_url[:-1]


#: Landing zone-related API.
landing_zones = SimpleNamespace(
    list=_landingzones_list, create=_landingzones_create, move=_landingzones_move
)


@attr.s(frozen=True, auto_attribs=True)
class IsaData:
    """Bundle together investigation, studies, assays from one project."""

    #: Investigation.
    investigation: InvestigationInfo
    #: Investigation file name.
    investigation_filename: str
    #: Tuple of studies.
    studies: typing.Dict[str, Study]
    #: Tuple of assays.
    assays: typing.Dict[str, Assay]


class _SheetClient:
    """Provide commands for sample sheets."""

    def __init__(self, owner):
        #: Owning ``Client`` instance.
        self.owner = owner

    def get_raw(self, project_uuid=None):
        """Get raw sample sheet data, ``project_uuid`` can override default ``project_uuid`` from ``self.owner``."""
        if not project_uuid and not self.owner.project_uuid:
            raise ParameterException("Both Client and method's project_uuid argument missing.")
        return _samplesheets_get(
            sodar_url=self.owner.sodar_url,
            sodar_api_token=self.owner.sodar_api_token,
            project_uuid=project_uuid or self.owner.project_uuid,
        )

    def get(self, project_uuid=None) -> IsaData:
        raw_data = self.get_raw(project_uuid)
        investigation = InvestigationReader.from_stream(
            input_file=io.StringIO(raw_data["investigation"]["tsv"]),
            filename=raw_data["investigation"]["path"],
        ).read()
        studies = {
            path: StudyReader.from_stream(
                study_id=path, input_file=io.StringIO(details["tsv"]), filename=path
            ).read()
            for path, details in raw_data["studies"].items()
        }
        if len(studies) > 1:  # pragma: nocover
            raise UnsupportedIsaTabFeatureException("More than one study found!")
        study = list(studies.values())[0]
        assays = {
            path: AssayReader.from_stream(
                study_id=study.file,
                assay_id=path,
                input_file=io.StringIO(details["tsv"]),
                filename=path,
            ).read()
            for path, details in raw_data["assays"].items()
        }
        return IsaData(investigation, raw_data["investigation"]["path"], studies, assays)
