
import csv
import io
import re
import time
from urllib.parse import urlparse, parse_qs

import requests
from flask import Flask, jsonify, render_template, request, Response
from rdflib import Graph, Namespace
from rdflib.namespace import DCTERMS, RDF

app = Flask(__name__)

CATALOG_URL = "https://opendata.iweps.be/statdcat-ap/walstat"
DATA_URL = "https://opendata.iweps.be/api/data"

DCAT = Namespace("http://www.w3.org/ns/dcat#")

session = requests.Session()
session.headers.update({
    "User-Agent": "Explorateur-IWEPS/1.0"
})

_catalog_cache = []
_catalog_loaded_at = 0
CACHE_SECONDS = 6 * 60 * 60


def get_text(graph, subject, predicate):
    values = graph.objects(subject, predicate)
    for value in values:
        text = str(value).strip()
        if text:
            return text
    return ""


def get_indicator_code(url):
    match = re.search(
        r"/api/data/(?:json|csv)/(\d{6}[_-]\d+)",
        str(url)
    )
    if match:
        return match.group(1).replace("-", "_")

    return ""


def load_catalog():
    global _catalog_cache, _catalog_loaded_at

    if _catalog_cache and time.time() - _catalog_loaded_at < CACHE_SECONDS:
        return _catalog_cache

    response = session.get(CATALOG_URL, timeout=60)
    response.raise_for_status()

    graph = Graph()
    content = response.content
    parsed = False

    for rdf_format in ("xml", "turtle", "json-ld", "n3"):
        try:
            graph.parse(data=content, format=rdf_format)
            parsed = True
            break
        except Exception:
            graph = Graph()

    if not parsed:
        raise ValueError("Le catalogue RDF n'a pas pu être lu.")

    indicators = {}

    for subject in graph.subjects():
        title = get_text(graph, subject, DCTERMS.title)

        if not title:
            continue

        description = get_text(graph, subject, DCTERMS.description)
        code = ""

        distributions = list(graph.objects(subject, DCAT.distribution))

        for distribution in distributions:
            for predicate in (DCAT.accessURL, DCAT.downloadURL):
                for url in graph.objects(distribution, predicate):
                    code = get_indicator_code(url)
                    if code:
                        break
                if code:
                    break
            if code:
                break

        if not code:
            parsed_url = urlparse(str(subject))
            params = parse_qs(parsed_url.query)
            base_id = params.get("indicateur_id", [""])[0]
            order = params.get("ordre", [""])[0]

            if base_id.isdigit() and order.isdigit():
                code = f"{base_id}_{order}"

        if not code:
            continue

        indicators[code] = {
            "id": code,
            "title": title,
            "name": title,
            "description": description
        }

    if not indicators:
        raise ValueError(
            "Aucun identifiant n'a pu être extrait du catalogue."
        )

    _catalog_cache = sorted(
        indicators.values(),
        key=lambda item: item["title"].casefold()
    )
    _catalog_loaded_at = time.time()

    return _catalog_cache


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "application": "Explorateur IWEPS"
    })


@app.route("/api/indicators")
def indicators():
    query = request.args.get("q", "").strip().casefold()

    try:
        catalog = load_catalog()
    except Exception:
        app.logger.exception("Impossible de charger le catalogue IWEPS")
        return jsonify({
            "indicators": [],
            "error": "Le catalogue IWEPS est temporairement inaccessible."
        }), 502

    if query:
        results = [
            item for item in catalog
            if query in item["title"].casefold()
            or query in item["description"].casefold()
            or query in item["id"].casefold()
        ]
    else:
        results = catalog

    limit = request.args.get("limit", "50")
    try:
        limit = max(1, min(int(limit), 100))
    except ValueError:
        limit = 50

    return jsonify({
        "count": len(results),
        "indicators": results[:limit],
        "message": (
            f"{len(results)} indicateur(s) trouvé(s). "
            "La liste affichée est limitée aux premiers résultats."
        )
    })


@app.route("/api/data")
def data():
    indicator = request.args.get("indicator", "").replace("-", "_")
    output_format = request.args.get("format", "json").lower()
    territory = request.args.get("territory", "reg").lower()
    period = request.args.get("period", "all").lower()

    if not re.fullmatch(r"\d{6}_\d+", indicator):
        return jsonify({
            "error": "Identifiant invalide. Exemple : 833605_7."
        }), 400

    if output_format not in ("json", "csv"):
        return jsonify({
            "error": "Le format doit être json ou csv."
        }), 400

    if period not in ("all", "last"):
        return jsonify({
            "error": "La période doit être all ou last."
        }), 400

    territory_options = {
        "reg": "ins=3000",
        "com": "com",
        "arr": "arr",
        "prov": "prov"
    }

    if territory not in territory_options:
        return jsonify({
            "error": "Territoire non reconnu."
        }), 400

    options = territory_options[territory] + f"+period={period}"
    url = f"{DATA_URL}/{output_format}/{indicator}/{options}"

    try:
        upstream = session.get(url, timeout=60)

        if upstream.status_code != 200:
            return jsonify({
                "error": (
                    "L'IWEPS n'a pas fourni de données pour "
                    "cet indicateur et ce territoire."
                )
            }), 502

        content_type = (
            "text/csv; charset=utf-8"
            if output_format == "csv"
            else "application/json; charset=utf-8"
        )

        return Response(
            upstream.content,
            status=200,
            content_type=content_type
        )

    except requests.RequestException:
        app.logger.exception("Erreur lors de la récupération des données")
        return jsonify({
            "error": "Impossible de contacter l'API IWEPS."
        }), 502


if __name__ == "__main__":
    import os
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
