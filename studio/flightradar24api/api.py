# -*- coding: utf-8 -*-

from typing import Any, Dict, List, Optional, Tuple, Union
import dataclasses
import math

from .core import Core
from .entities.airport import Airport
from .entities.flight import Flight
from .errors import AirportNotFoundError, LoginError
from .request import APIRequest


@dataclasses.dataclass
class FlightTrackerConfig:
    faa: str = "1"
    satellite: str = "1"
    mlat: str = "1"
    flarm: str = "1"
    adsb: str = "1"
    gnd: str = "1"
    air: str = "1"
    vehicles: str = "1"
    estimated: str = "1"
    maxage: str = "14400"
    gliders: str = "1"
    stats: str = "1"
    limit: str = "5000"


class FlightRadar24API:
    def __init__(self, user: Optional[str] = None, password: Optional[str] = None):
        self.__flight_tracker_config = FlightTrackerConfig()
        self.__login_data: Optional[Dict] = None
        if user is not None and password is not None:
            self.login(user, password)

    def get_airlines(self) -> List[Dict]:
        return APIRequest(Core.airlines_data_url, headers=Core.json_headers).get_content()["rows"]

    def get_airport(self, code: str, *, details: bool = False) -> Airport:
        if not (3 <= len(code) <= 4):
            raise ValueError(f"Invalid airport code: '{code}'")
        if details:
            airport = Airport()
            airport.set_airport_details(self.get_airport_details(code))
            return airport
        response = APIRequest(Core.airport_data_url.format(code), headers=Core.json_headers)
        content = response.get_content()
        if not content or not isinstance(content, dict) or not content.get("details"):
            raise AirportNotFoundError(f"Could not find airport: '{code}'")
        return Airport(info=content["details"])

    def get_airport_details(self, code: str, flight_limit: int = 100, page: int = 1,
                            timestamp: Optional[int] = None) -> Dict:
        if not (3 <= len(code) <= 4):
            raise ValueError(f"Invalid airport code: '{code}'")
        params = {"format": "json", "code": code, "limit": flight_limit, "page": page}
        if timestamp is not None:
            params["timestamp"] = timestamp
        if self.__login_data:
            params["token"] = self.__login_data["cookies"]["_frPl"]
        response = APIRequest(Core.api_airport_data_url, params, Core.json_headers, exclude_status_codes=[400])
        content: Dict = response.get_content()
        if response.get_status_code() == 400 and content.get("errors"):
            errors = content["errors"]["errors"]["parameters"]
            if errors.get("limit"):
                raise ValueError(errors["limit"]["notBetween"])
            raise AirportNotFoundError(f"Could not find airport: '{code}'", errors)
        result = content["result"]["response"]
        data = result.get("airport", {}).get("pluginData", {})
        if "details" not in data and not data.get("runways") and len(data) <= 3:
            raise AirportNotFoundError(f"Could not find airport: '{code}'")
        return result

    def get_airport_disruptions(self) -> Dict:
        return APIRequest(Core.airport_disruptions_url, headers=Core.json_headers).get_content()

    def get_airports(self) -> List[Airport]:
        response = APIRequest(Core.airports_data_url, headers=Core.json_headers)
        return [Airport(basic_info=a) for a in response.get_content()["rows"]]

    def get_bookmarks(self) -> Dict:
        if not self.is_logged_in():
            raise LoginError("Must be logged in.")
        headers = Core.json_headers.copy()
        headers["accesstoken"] = self.get_login_data()["accessToken"]
        return APIRequest(Core.bookmarks_url, headers=headers, cookies=self.__login_data["cookies"]).get_content()

    def get_bounds(self, zone: Dict[str, float]) -> str:
        return "{},{},{},{}".format(zone["tl_y"], zone["br_y"], zone["tl_x"], zone["br_x"])

    def get_bounds_by_point(self, latitude: float, longitude: float, radius: float) -> str:
        half = abs(radius) / 1000
        lat, lon = math.radians(latitude), math.radians(longitude)
        R = 6371
        h = math.sqrt(2 * half ** 2)

        def _corner(bearing_deg):
            b = math.radians(bearing_deg)
            new_lat = math.asin(math.sin(lat) * math.cos(h / R) + math.cos(lat) * math.sin(h / R) * math.cos(b))
            new_lon = lon + math.atan2(math.sin(b) * math.sin(h / R) * math.cos(lat),
                                       math.cos(h / R) - math.sin(lat) * math.sin(new_lat))
            return math.degrees(new_lat), math.degrees(new_lon)

        lat_max, lon_min = _corner(45)
        lat_min, lon_max = _corner(225)
        return self.get_bounds({"tl_y": lat_max, "br_y": lat_min, "tl_x": lon_min, "br_x": lon_max})

    def get_flight_details(self, flight: Flight) -> Dict[Any, Any]:
        return APIRequest(Core.flight_data_url.format(flight.id), headers=Core.json_headers).get_content()

    def get_flight_by_number(self, flight_number: str) -> Dict[Any, Any]:
        """Fetch flight history/schedule by flight number (callsign)."""
        params = {"fetchBy": "flight"}
        if self.__login_data:
            params["token"] = self.__login_data["cookies"]["_frPl"]
        url = Core.api_flightradar_base_url + f"/flight/list.json?query={flight_number}"
        response = APIRequest(url, params, headers=Core.json_headers)
        content: Dict = response.get_content()
        return content.get("result", {}).get("response", {})

    def get_rego_details(self, aircraft: str) -> Dict[Any, Any]:
        params = {}
        if self.__login_data:
            params["token"] = self.__login_data["cookies"]["_frPl"]
        response = APIRequest(Core.aircraft_detail_url.format(aircraft), params, headers=Core.json_headers)
        content: Dict = response.get_content()
        if response.get_status_code() == 400 and content.get("errors"):
            errors = content["errors"]["errors"]["parameters"]
            if errors.get("limit"):
                raise ValueError(errors["limit"]["notBetween"])
            raise AirportNotFoundError(f"Could not find aircraft: '{aircraft}'", errors)
        return content["result"]["response"]

    def get_flight_tracker_config(self) -> FlightTrackerConfig:
        return dataclasses.replace(self.__flight_tracker_config)

    def get_zones(self) -> Dict[str, Dict]:
        zones = APIRequest(Core.zones_data_url, headers=Core.json_headers).get_content()
        zones.pop("version", None)
        return zones

    def search(self, query: str, limit: int = 50) -> Dict:
        response = APIRequest(Core.search_data_url.format(query, limit), headers=Core.json_headers).get_content()
        results = response.get("results", [])
        stats = response.get("stats", {})
        i, counted, data = 0, 0, {}
        for name, count in stats.get("count", {}).items():
            data[name] = []
            while i < counted + count and i < len(results):
                data[name].append(results[i])
                i += 1
            counted += count
        return data

    def is_logged_in(self) -> bool:
        return self.__login_data is not None

    def login(self, user: str, password: str) -> None:
        data = {"email": user, "password": password, "remember": "true", "type": "web"}
        response = APIRequest(Core.user_login_url, headers=Core.json_headers, data=data)
        content = response.get_content()
        if not str(response.get_status_code()).startswith("2") or not content.get("success"):
            raise LoginError(content.get("message", "Incorrect email or password"))
        self.__login_data = {"userData": content["userData"], "cookies": response.get_cookies()}

    def logout(self) -> bool:
        if self.__login_data is None:
            return True
        cookies = self.__login_data["cookies"]
        self.__login_data = None
        r = APIRequest(Core.user_login_url, headers=Core.json_headers, cookies=cookies)
        return str(r.get_status_code()).startswith("2")

    def get_login_data(self) -> Dict[Any, Any]:
        if not self.is_logged_in():
            raise LoginError("Must be logged in.")
        return self.__login_data["userData"].copy()

    def set_flight_tracker_config(
        self,
        flight_tracker_config: Optional[FlightTrackerConfig] = None,
        **config: Union[int, str],
    ) -> None:
        if flight_tracker_config is not None:
            self.__flight_tracker_config = flight_tracker_config
        current = dataclasses.asdict(self.__flight_tracker_config)
        for key, value in config.items():
            value = str(value)
            if key not in current:
                raise KeyError(f"Unknown option: '{key}'")
            if not value.isdecimal():
                raise TypeError(f"Value must be decimal, got '{key}'")
            setattr(self.__flight_tracker_config, key, value)
