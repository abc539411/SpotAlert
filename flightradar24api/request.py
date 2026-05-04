# -*- coding: utf-8 -*-

from typing import Dict, List, Optional, Union

import brotli
import gzip
import json

import cloudscraper
import requests.structures

from .errors import CloudflareError

# Single shared scraper session — reuses Cloudflare clearance cookies across requests
_scraper = cloudscraper.create_scraper()


class APIRequest:
    __content_encodings = {
        "": lambda x: x,
        "br": brotli.decompress,
        "gzip": gzip.decompress,
    }

    def __init__(
        self,
        url: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        data: Optional[Dict] = None,
        cookies: Optional[Dict] = None,
        exclude_status_codes: List[int] = list(),
    ):
        self.url = url
        request_method = _scraper.get if data is None else _scraper.post

        if params:
            sep = "&" if "?" in url else "?"
            url += sep + "&".join(f"{k}={v}" for k, v in params.items())

        # Do not pass custom headers — cloudscraper must own all headers for Cloudflare bypass
        self.__response = request_method(url, cookies=cookies, data=data)

        if self.get_status_code() == 520:
            raise CloudflareError(
                message="An unexpected error occurred. You may be making too many requests.",
                response=self.__response,
            )

        if self.get_status_code() not in exclude_status_codes:
            self.__response.raise_for_status()

    def get_content(self) -> Union[Dict, bytes]:
        content = self.__response.content
        content_encoding = self.__response.headers.get("Content-Encoding", "")
        content_type = self.__response.headers.get("Content-Type", "")

        try:
            content = self.__content_encodings[content_encoding](content)
        except Exception:
            pass

        if "application/json" in content_type:
            return json.loads(content)
        return content

    def get_cookies(self) -> Dict:
        return self.__response.cookies.get_dict()

    def get_headers(self) -> requests.structures.CaseInsensitiveDict:
        return self.__response.headers

    def get_response_object(self) -> requests.models.Response:
        return self.__response

    def get_status_code(self) -> int:
        return self.__response.status_code
