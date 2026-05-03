This is a modified version of the FlightRadarAPI Python library by JeanExtreme002.

Original repository: https://github.com/JeanExtreme002/FlightRadarAPI/tree/main/python

Modifications made for this project:
- Replaced `requests` with `cloudscraper` in `request.py` to bypass Cloudflare bot protection on api.flightradar24.com
- Removed custom headers (any headers break the Cloudflare bypass — no headers must be passed)
