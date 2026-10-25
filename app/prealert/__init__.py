"""The unofficial pre-alert: levels computed by Vigie from an open forecast model, never an official warning.

A pilot for Turkey ([ADR 0011](../../docs/decisions/0011-unofficial-pre-alert-turkey-shadow-pilot.md) and its amendment): computed
from ECMWF open data and stored, shown only by the experimental route ``GET /v1/experimental/prealert`` of an instance that runs it,
for testers (labelled experimental and unofficial), until the criteria written in the ADR are met. Decoding the model files needs the optional dependencies
``numpy`` and ``eccodes`` (``pip install .[prealert]``); nothing here is imported by the application unless the pilot is on.
"""
