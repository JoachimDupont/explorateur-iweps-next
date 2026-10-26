import re, time
from urllib.parse import urlparse, parse_qs
import requests
from flask import Flask, jsonify, render_template, request, Response
from rdflib import Graph, Namespace
from rdflib.namespace import DCTERMS, RDFS

app = Flask(__name__)
CATALOG_URL = "https://opendata.iweps.be/statdcat-ap/walstat"
DATA_URL = "https://opendata.iweps.be/api/data"
DCAT = Namespace("http://www.w3.org/ns/dcat#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
session = requests.Session()
session.headers.update({"User-Agent": "Explorateur-IWEPS/2.1"})
_catalog, _loaded = [], 0
CACHE_SECONDS = 21600

# Thèmes affichés dans WalStat. Si le catalogue RDF expose un thème explicite,
# celui-ci est prioritaire ; sinon, un classement indicatif par mots-clés est utilisé.
WALSTAT_THEMES = [
    "Population et migration", "Territoire", "Niveaux et conditions de vie",
    "Activité économique", "Marché du travail", "Santé", "Logement",
    "Environnement", "Mobilité", "Enseignement", "Petite enfance", "Énergie",
    "Agriculture et ruralité", "Culture", "Tourisme",
    "Non-marchand et économie sociale", "Pouvoirs locaux", "Citoyenneté",
    "Autres indicateurs"
]
THEME_KEYWORDS = {
    "Population et migration": ["population", "âge", "age moyen", "migration", "naissance", "décès", "deces", "ménage", "menage", "nationalité", "nationalite", "fécondité", "fecondite"],
    "Territoire": ["territoire", "superficie", "densité", "densite", "occupation du sol", "quartier", "urbanisation"],
    "Niveaux et conditions de vie": ["revenu", "pauvreté", "pauvrete", "bien-être", "bien etre", "conditions de vie", "inégalité", "inegalite", "précarité", "precarite", "isadf", "logement social"],
    "Activité économique": ["entreprise", "établissement", "etablissement", "activité économique", "activite economique", "chiffre d'affaires", "chiffre d affaires", "valeur ajoutée", "valeur ajoutee", "pib", "création", "creation d'entreprise"],
    "Marché du travail": ["emploi", "chômage", "chomage", "activité", "activite", "travailleur", "demandeur d'emploi", "demandeur d emploi", "insertion", "salarié", "salarie", "profession"],
    "Santé": ["santé", "sante", "mortalité", "mortalite", "médecin", "medecin", "hôpital", "hopital", "maladie", "soin", "espérance de vie", "esperance de vie"],
    "Logement": ["logement", "habitation", "loyer", "bâtiment résidentiel", "batiment residentiel", "parc immobilier"],
    "Environnement": ["environnement", "déchet", "dechet", "eau", "air", "pollution", "biodiversité", "biodiversite", "climat", "sol artificialisé", "sol artificialise"],
    "Mobilité": ["mobilité", "mobilite", "transport", "déplacement", "deplacement", "voiture", "vélo", "velo", "train", "bus", "navette", "motorisation"],
    "Enseignement": ["enseignement", "éducation", "education", "école", "ecole", "scolaire", "diplôme", "diplome", "étudiant", "etudiant", "formation"],
    "Petite enfance": ["petite enfance", "crèche", "creche", "accueil de l'enfance", "accueil de l enfance", "milieu d'accueil", "milieu d accueil"],
    "Énergie": ["énergie", "energie", "électricité", "electricite", "gaz", "consommation énergétique", "consommation energetique", "renouvelable"],
    "Agriculture et ruralité": ["agriculture", "agricole", "rural", "ruralité", "ruralite", "exploitation agricole", "surface agricole"],
    "Culture": ["culture", "bibliothèque", "bibliotheque", "musée", "musee", "patrimoine", "spectacle", "lecture"],
    "Tourisme": ["tourisme", "touristique", "hébergement touristique", "hebergement touristique", "nuitée", "nuitee", "visiteur"],
    "Non-marchand et économie sociale": ["non-marchand", "non marchand", "économie sociale", "economie sociale", "associatif", "asbl", "volontariat"],
    "Pouvoirs locaux": ["commune", "communal", "province", "finances communales", "budget communal", "dette communale", "pouvoir local", "fiscalité locale", "fiscalite locale"],
    "Citoyenneté": ["citoyenneté", "citoyennete", "élection", "election", "vote", "participation citoyenne", "nationalité", "nationalite"],
}

def get_text(graph, subject, predicate):
    for value in graph.objects(subject, predicate):
        if str(value).strip():
            return str(value).strip()
    return ""

def label_for(graph, node):
    if isinstance(node, (str,)):
        return node.strip()
    for pred in (SKOS.prefLabel, RDFS.label, DCTERMS.title):
        for value in graph.objects(node, pred):
            if str(value).strip():
                return str(value).strip()
    return str(node).rstrip("/").split("/")[-1].replace("_", " ").strip()

def norm(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s).casefold()) if unicodedata.category(c) != "Mn")

def classify_theme(title, description, explicit_theme=""):
    if explicit_theme:
        clean = explicit_theme.strip()
        # Map theme labels/URIs to the WalStat spelling where possible.
        for known in WALSTAT_THEMES:
            if norm(clean) == norm(known) or norm(known) in norm(clean):
                return known, "Métadonnée du catalogue"
        return clean, "Métadonnée du catalogue"
    text = norm(title + " " + description)
    for theme, keywords in THEME_KEYWORDS.items():
        if any(norm(word) in text for word in keywords):
            return theme, "Classement indicatif par mots-clés"
    return "Autres indicateurs", "À vérifier"

def get_code(url):
    m = re.search(r"/api/data/(?:json|csv)/(\d{6}[_-]\d+)", str(url))
    return m.group(1).replace("-", "_") if m else ""

def load_catalog():
    global _catalog, _loaded
    if _catalog and time.time() - _loaded < CACHE_SECONDS:
        return _catalog
    response = session.get(CATALOG_URL, timeout=60)
    response.raise_for_status()
    graph, parsed = Graph(), False
    for fmt in ("xml", "turtle", "json-ld", "n3"):
        try:
            graph.parse(data=response.content, format=fmt)
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
        for dist in graph.objects(subject, DCAT.distribution):
            for pred in (DCAT.accessURL, DCAT.downloadURL):
                for url in graph.objects(dist, pred):
                    code = get_code(url)
                    if code:
                        break
                if code:
                    break
            if code:
                break
        if not code:
            p = parse_qs(urlparse(str(subject)).query)
            base, order = p.get("indicateur_id", [""])[0], p.get("ordre", [""])[0]
            if base.isdigit() and order.isdigit():
                code = f"{base}_{order}"
        if not code:
            continue

        explicit_theme = ""
        for pred in (DCAT.theme, DCTERMS.subject):
            nodes = list(graph.objects(subject, pred))
            if nodes:
                explicit_theme = label_for(graph, nodes[0])
                break
        theme, theme_source = classify_theme(title, description, explicit_theme)
        # The part before a colon is a useful initial sub-theme, but is not
        # presented as an official IWEPS classification.
        subtheme = title.split(":", 1)[0].strip() if ":" in title and len(title.split(":", 1)[0].strip()) <= 90 else "Général"
        indicators[code] = {
            "id": code, "title": title, "name": title, "description": description,
            "theme": theme, "subtheme": subtheme or "Général",
            "themeSource": theme_source
        }
    if not indicators:
        raise ValueError("Aucun identifiant n'a pu être extrait du catalogue.")
    _catalog = sorted(indicators.values(), key=lambda x: x["title"].casefold())
    _loaded = time.time()
    return _catalog

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "application": "Explorateur IWEPS"})

@app.route("/api/indicators")
def indicators():
    q = request.args.get("q", "").strip().casefold()
    try:
        catalog = load_catalog()
    except Exception:
        app.logger.exception("Catalogue IWEPS inaccessible")
        return jsonify({"indicators": [], "themes": [], "error": "Le catalogue IWEPS est temporairement inaccessible."}), 502
    found = [x for x in catalog if not q or q in x["title"].casefold() or q in x["description"].casefold() or q in x["id"].casefold() or q in x.get("theme", "").casefold() or q in x.get("subtheme", "").casefold()]
    try:
        limit = max(1, min(int(request.args.get("limit", "1000")), 2000))
    except ValueError:
        limit = 1000
    themes = sorted({x["theme"] for x in catalog}, key=lambda x: x.casefold())
    return jsonify({
        "count": len(found), "total": len(catalog), "themes": themes,
        "indicators": found[:limit],
        "message": f"{len(found)} indicateur(s) trouvé(s)."
    })

@app.route("/api/data")
def data():
    indicator = request.args.get("indicator", "").replace("-", "_")
    fmt = request.args.get("format", "json").lower()
    territory = request.args.get("territory", "com").lower()
    period = request.args.get("period", "all").lower()
    if not re.fullmatch(r"\d{6}_\d+", indicator):
        return jsonify({"error": "Identifiant invalide. Exemple : 831110_1."}), 400
    if fmt not in ("json", "csv"):
        return jsonify({"error": "Le format doit être json ou csv."}), 400
    if period not in ("all", "last"):
        return jsonify({"error": "La période doit être all ou last."}), 400
    levels = {"reg": "ins=3000", "com": "com", "arr": "arr", "prov": "prov"}
    if territory not in levels:
        return jsonify({"error": "Territoire non reconnu."}), 400
    url = f"{DATA_URL}/{fmt}/{indicator}/{levels[territory]}+period={period}"
    try:
        r = session.get(url, timeout=60)
        if r.status_code != 200:
            return jsonify({"error": "L'IWEPS ne fournit pas de données pour cet indicateur et ce territoire, ou le service est temporairement indisponible."}), 502
        ct = "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8"
        return Response(r.content, status=200, content_type=ct)
    except requests.RequestException:
        app.logger.exception("Erreur API IWEPS")
        return jsonify({"error": "Impossible de contacter l'API IWEPS."}), 502

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
