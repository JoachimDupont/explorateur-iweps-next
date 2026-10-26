from flask import Flask, jsonify, render_template, request, Response

app = Flask(__name__)
_catalog = []
_loaded = 0
CACHE_SECONDS = 21600
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
            indicators[code] = {
                "id": code,
                "title": title,
                "name": title,
                "description": description,
                "theme": classify(title, description),
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
