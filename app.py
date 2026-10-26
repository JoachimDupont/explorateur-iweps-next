import os
import re
import time
import logging
from urllib.parse import urlparse, parse_qs

import requests
from flask import Flask, jsonify, render_template, request, Response
from rdflib import Graph, Namespace
from rdflib.namespace import DCTERMS

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

CATALOG_URL = "https://opendata.iweps.be/statdcat-ap/walstat"
DATA_URL = "https://opendata.iweps.be/api/data"
DCAT = Namespace("http://www.w3.org/ns/dcat#")
session = requests.Session()
session.headers.update({"User-Agent": "Explorateur-IWEPS/3.0"})
_catalog = []
_loaded = 0
CACHE_SECONDS = 21600

# Classification indicative fondée sur le vocabulaire des titres/descriptions.
# Elle facilite la navigation, sans prétendre remplacer les thèmes officiels IWEPS.
THEMES = [
    ("Population", ["population", "démograph", "naissance", "décès", "ménage", "migration", "âge", "vieillissement", "nationalité"]),
    ("Emploi et marché du travail", ["emploi", "chômage", "chomeur", "travail", "salari", "onss", "activité", "inactivité", "profession", "métier"]),
    ("Économie et entreprises", ["entreprise", "économ", "pib", "valeur ajoutée", "établissement", "faillite", "création", "commerce", "revenu cadastral"]),
    ("Revenus et précarité", ["revenu", "pauvreté", "precar", "précar", "inégalité", "allocation", "bénéficiaire", "endettement", "risque de pauvreté"]),
    ("Logement et immobilier", ["logement", "habitat", "loyer", "immobilier", "bâtiment", "batiment", "construction", "ménage privé", "parc de logements"]),
    ("Santé et bien-être", ["santé", "sante", "mortalité", "mortalite", "médecin", "medecin", "hôpital", "hopital", "handicap", "espérance de vie", "bien-être"]),
    ("Enseignement et formation", ["enseignement", "éducation", "education", "école", "ecole", "diplôme", "diplome", "formation", "étudiant", "etudiant", "scolar"]),
    ("Mobilité et transport", ["mobilité", "mobilite", "transport", "voiture", "déplacement", "deplacement", "route", "train", "vélo", "velo", "permis de conduire"]),
    ("Environnement et énergie", ["environnement", "énergie", "energie", "émission", "emission", "déchet", "dechet", "eau", "air", "climat", "sol", "biodiversité", "biodiversite"]),
    ("Territoire et services", ["territoire", "superficie", "densité", "densite", "administration", "service public", "équipement", "equipement", "accessibilité", "accessibilite", "commune"]),
]


def get_text(graph, subject, predicate):
    for value in graph.objects(subject, predicate):
        if str(value).strip():
            return str(value).strip()
    return ""


def get_code(url):
    match = re.search(r"/api/data/(?:json|csv)/(\d{6}[_-]\d+)", str(url))
    if match:
        return match.group(1).replace("-", "_")
    query = parse_qs(urlparse(str(url)).query)
    base = query.get("indicateur_id", [""])[0]
    order = query.get("ordre", [""])[0]
    return f"{base}_{order}" if base.isdigit() and order.isdigit() else ""


def classify(title, description=""):
    text = f"{title} {description}".casefold()
    for theme, keywords in THEMES:
        if any(word in text for word in keywords):
            return theme
    return "Autres indicateurs"


def load_catalog():
    global _catalog, _loaded
    if _catalog and time.time() - _loaded < CACHE_SECONDS:
        return _catalog

    response = session.get(CATALOG_URL, timeout=60)
    response.raise_for_status()
    graph = Graph()
    parsed = False
    for fmt in ("xml", "turtle", "json-ld", "n3", "nt"):
        try:
            candidate = Graph()
            candidate.parse(data=response.content, format=fmt)
            if len(candidate):
                graph, parsed = candidate, True
                break
        except Exception:
            continue
    if not parsed:
        raise ValueError("Le catalogue RDF IWEPS n'a pas pu être lu.")

    indicators = {}
    for subject in graph.subjects():
        title = get_text(graph, subject, DCTERMS.title)
        if not title:
            continue
        description = get_text(graph, subject, DCTERMS.description)
        code = ""
        for distribution in graph.objects(subject, DCAT.distribution):
            for predicate in (DCAT.accessURL, DCAT.downloadURL):
                for url in graph.objects(distribution, predicate):
                    code = get_code(url)
                    if code:
                        break
                if code:
                    break
            if code:
                break
        if not code:
            code = get_code(subject)
        if code:
            # Utilise les liens de métadonnées lorsqu'ils existent pour regrouper
            # les indicateurs appartenant à un même jeu/série de données.
            dataset = ""
            for relation in (DCTERMS.isPartOf, DCTERMS.source, DCAT.inSeries, DCAT.dataset):
                related = next(iter(graph.objects(subject, relation)), None)
                if related is not None:
                    dataset = get_text(graph, related, DCTERMS.title) or get_text(graph, related, DCTERMS.identifier)
                    if not dataset and not str(related).startswith(("http://", "https://", "urn:")):
                        dataset = str(related).strip()
                    if dataset:
                        break
            indicators[code] = {
                "id": code,
                "title": title,
                "name": title,
                "description": description,
                "theme": classify(title, description),
                "dataset": dataset,
            }

    if not indicators:
        raise ValueError("Aucun identifiant d'indicateur n'a été extrait du catalogue.")
    _catalog = sorted(indicators.values(), key=lambda item: item["title"].casefold())
    _loaded = time.time()
    app.logger.info("Catalogue IWEPS chargé : %s indicateurs", len(_catalog))
    return _catalog


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "application": "Explorateur IWEPS"})


@app.route("/api/indicators")
def indicators():
    query = request.args.get("q", "").strip().casefold()
    theme = request.args.get("theme", "").strip()
    try:
        catalog = load_catalog()
    except Exception:
        app.logger.exception("Erreur lors du chargement du catalogue IWEPS")
        return jsonify({"indicators": [], "error": "Impossible de charger le catalogue IWEPS. Réessaie plus tard."}), 502

    found = [item for item in catalog if
             (not query or query in item["title"].casefold() or query in item["description"].casefold() or query in item["id"].casefold())
             and (not theme or item["theme"] == theme)]
    try:
        limit = max(1, min(int(request.args.get("limit", "100")), 500))
    except ValueError:
        limit = 100
    themes = sorted({item["theme"] for item in catalog})
    return jsonify({"count": len(found), "indicators": found[:limit], "themes": themes})


@app.route("/api/data")
def data():
    indicator = request.args.get("indicator", "").replace("-", "_")
    fmt = request.args.get("format", "json").lower()
    territory = request.args.get("territory", "com").lower()
    period = request.args.get("period", "all").lower()
    if not re.fullmatch(r"\d{6}_\d+", indicator):
        return jsonify({"error": "Identifiant d'indicateur invalide."}), 400
    if fmt not in ("json", "csv"):
        return jsonify({"error": "Format invalide."}), 400
    if period not in ("all", "last"):
        return jsonify({"error": "Période invalide."}), 400
    levels = {"reg": "ins=3000", "com": "com", "arr": "arr", "prov": "prov"}
    if territory not in levels:
        return jsonify({"error": "Territoire non reconnu."}), 400
    url = f"{DATA_URL}/{fmt}/{indicator}/{levels[territory]}+period={period}"
    try:
        response = session.get(url, timeout=60)
        if response.status_code != 200:
            app.logger.warning("API IWEPS statut %s pour %s", response.status_code, url)
            return jsonify({"error": "Aucune donnée disponible pour cet indicateur et ce territoire, ou service IWEPS temporairement indisponible."}), 502
        content_type = "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8"
        return Response(response.content, status=200, content_type=content_type)
    except requests.RequestException:
        app.logger.exception("Erreur de connexion à l'API IWEPS")
        return jsonify({"error": "Impossible de contacter l'API IWEPS."}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
