# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
"""
HTTP TI Provider base.

Input can be a single IoC observable or a pandas DataFrame containing
multiple observables. Processing may require a an API key and
processing performance may be limited to a specific number of
requests per minute for the account type that you have.

"""
import abc
import traceback
from functools import lru_cache
from http import client
from json import JSONDecodeError
from typing import Any, Dict, List, Tuple

import attr
import httpx
from attr import Factory

from ..._version import VERSION
from ...common.exceptions import MsticpyConfigException
from ...common.pkg_config import get_http_timeout
from ...common.utility import export, mp_ua_header
from .lookup_result import LookupResult, LookupStatus
from .result_severity import ResultSeverity
from .ti_provider_base import TIProvider

__version__ = VERSION
__author__ = "Ian Hellen"


# pylint: disable=too-few-public-methods
@attr.s(auto_attribs=True)
class IoCLookupParams:
    """IoC HTTP Lookup Params definition."""

    path: str = ""
    verb: str = "GET"
    full_url: bool = False
    headers: Dict[str, str] = Factory(dict)
    params: Dict[str, str] = Factory(dict)
    data: Dict[str, str] = Factory(dict)
    auth_type: str = ""
    auth_str: List[str] = Factory(list)
    sub_type: str = ""


@export
class HttpTIProvider(TIProvider, abc.ABC):
    """HTTP API Lookup provider base class."""

    _BASE_URL = ""

    _IOC_QUERIES: Dict[str, IoCLookupParams] = {}

    _REQUIRED_PARAMS: List[str] = []

    def __init__(self, **kwargs):
        """Initialize a new instance of the class."""
        super().__init__(**kwargs)

        self._httpx_client = httpx.Client(timeout=get_http_timeout(**kwargs))
        self._request_params = {}
        if "ApiID" in kwargs:
            api_id = kwargs.pop("ApiID")
            self._request_params["API_ID"] = api_id.strip() if api_id else None
        if "AuthKey" in kwargs:
            auth_key = kwargs.pop("AuthKey")
            self._request_params["API_KEY"] = auth_key.strip() if auth_key else None

        missing_params = [
            param
            for param in self._REQUIRED_PARAMS
            if param not in self._request_params
        ]
        if missing_params:
            param_list = ", ".join(f"'{param}'" for param in missing_params)
            raise MsticpyConfigException(
                f"Parameter values missing for Provider '{self.__class__.__name__}'",
                f"Missing parameters are: {param_list}",
            )

    @lru_cache(maxsize=256)
    def lookup_ioc(  # type: ignore
        self, ioc: str, ioc_type: str = None, query_type: str = None, **kwargs
    ) -> LookupResult:
        """
        Lookup a single item.

        Parameters
        ----------
        ioc : str
            Item value to lookup
        ioc_type : str, optional
            The Type of the value to lookup, by default None (type will be inferred)
        query_type : str, optional
            Specify the data subtype to be queried, by default None.
            If not specified the default record type for the item_value
            will be returned.

        Returns
        -------
        LookupResult
            The lookup result:
            result - Positive/Negative,
            details - Lookup Details (or status if failure),
            raw_result - Raw Response
            reference - URL of the item

        Raises
        ------
        NotImplementedError
            If attempting to use an HTTP method or authentication
            protocol that is not supported.

        Notes
        -----
        Note: this method uses memoization (lru_cache) to cache results
        for a particular observable to try avoid repeated network calls for
        the same item.

        """
        result = self._check_ioc_type(
            ioc=ioc, ioc_type=ioc_type, query_subtype=query_type
        )

        result.provider = kwargs.get("provider_name", self.__class__.__name__)
        if result.status != LookupStatus.OK.value:
            return result

        req_params: Dict[str, Any] = {}
        try:
            verb, req_params = self._substitute_parms(
                result.safe_ioc, result.ioc_type, query_type
            )
            if verb == "GET":
                response = self._httpx_client.get(
                    **req_params, timeout=get_http_timeout(**kwargs)
                )
            else:
                raise NotImplementedError(f"Unsupported verb {verb}")
            result.status = response.status_code
            result.reference = req_params["url"]
            if result.status == 200:
                try:
                    result.raw_result = response.json()
                    result.result, severity, result.details = self.parse_results(result)
                except JSONDecodeError:
                    result.raw_result = f"""There was a problem parsing results from this lookup:
                                        {response.text}"""
                    result.result = False
                    severity = ResultSeverity.information
                    result.details = {}
                result.set_severity(severity)
                result.status = LookupStatus.OK.value
            else:
                result.raw_result = str(response)
                result.result = False
                result.details = self._response_message(result.status)
            return result
        except (
            LookupError,
            JSONDecodeError,
            NotImplementedError,
            ConnectionError,
        ) as err:
            self._err_to_results(result, err)
            if not isinstance(err, LookupError):
                url = req_params.get("url", None) if req_params else None
                result.reference = url
            return result

    # pylint: enable=duplicate-code
    def _substitute_parms(
        self, value: str, value_type: str, query_type: str = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Create requests parameters collection.

        Parameters
        ----------
        value : str
            The value of the item being queried
        value_type : str, optional
            The value type, by default None
        query_type : str, optional
            Specify the data subtype to be queried, by default None.
            If not specified the default record type for the type
            will be returned.

        Returns
        -------
        Tuple[str, Dict[str, Any]]
            HTTP method, dictionary of parameter keys/values

        """
        req_params = {"observable": value}
        req_params.update(self._request_params)
        value_key = f"{value_type}-{query_type}" if query_type else value_type
        src = self.ioc_query_defs.get(value_key, None)
        if not src:
            raise LookupError(f"Provider does not support this type {value_key}.")

        # create a parameter dictionary to pass to requests
        # substitute any parameter value from our req_params dict
        req_dict: Dict[str, Any] = {
            "headers": {},
            "url": src.path.format(observable=value)
            if src.full_url
            else self._BASE_URL + src.path.format(**req_params),
        }

        if src.headers:
            headers: Dict[str, Any] = {
                key: val.format(**req_params) for key, val in src.headers.items()
            }
            req_dict["headers"] = headers
        if "User-Agent" not in req_dict["headers"]:
            req_dict["headers"].update(mp_ua_header())
        if src.params:
            q_params: Dict[str, Any] = {
                key: val.format(**req_params) for key, val in src.params.items()
            }
            req_dict["params"] = q_params
        if src.data:
            q_data: Dict[str, Any] = {
                key: val.format(**req_params) for key, val in src.data.items()
            }
            req_dict["data"] = q_data
        if src.auth_type and src.auth_str:
            auth_strs: Tuple = tuple(p.format(**req_params) for p in src.auth_str)
            if src.auth_type == "HTTPBasic":
                req_dict["auth"] = auth_strs
            else:
                raise NotImplementedError(f"Unknown auth type {src.auth_type}")
        return src.verb, req_dict

    @abc.abstractmethod
    def parse_results(self, response: LookupResult) -> Tuple[bool, ResultSeverity, Any]:
        """
        Return the details of the response.

        Parameters
        ----------
        response : LookupResult
            The returned data response

        Returns
        -------
        Tuple[bool, ResultSeverity, Any]
            bool = positive or negative hit
            ResultSeverity = enumeration of severity
            Object with match details

        """

    @staticmethod
    def _failed_response(response: LookupResult) -> bool:
        """
        Return True if negative response.

        Parameters
        ----------
        response : LookupResult
            The returned data response

        Returns
        -------
        bool
            True if the response indicated failure.

        """
        return (
            response.status != 200
            or not response.raw_result
            or not isinstance(response.raw_result, dict)
        )

    @staticmethod
    def _err_to_results(result: LookupResult, err: Exception):
        result.details = err.args
        result.raw_result = (
            type(err).__name__ + "\n" + str(err) + "\n" + traceback.format_exc()
        )

    @staticmethod
    def _response_message(status_code):
        if status_code == 404:
            return "Not found."
        if status_code == 401:
            return "Authorization failed. Check account and key details."
        if status_code == 403:
            return "Request forbidden. Allowed query rate may have been exceeded."
        return client.responses.get(status_code, "Unknown HTTP status code.")
