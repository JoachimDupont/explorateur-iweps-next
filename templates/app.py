import re, time
from urllib.parse import urlparse, parse_qs
import requests
from flask import Flask, jsonify, render_template, request, Response
from rdflib import Graph, Namespace
from rdflib.namespace import DCTERMS

app = Flask(__name__)
CATALOG_URL = "https://opendata.iweps.be/statdcat-ap/walstat"
DATA_URL = "https://opendata.iweps.be/api/data"
DCAT = Namespace("http://www.w3.org/ns/dcat#")
session = requests.Session()
session.headers.update({"User-Agent": "Explorateur-IWEPS/2.0"})
_catalog, _loaded = [], 0
CACHE_SECONDS = 21600

def get_text(graph, subject, predicate):
    for value in graph.objects(subject, predicate):
        if str(value).strip(): return str(value).strip()
    return ""

def get_code(url):
    m = re.search(r"/api/data/(?:json|csv)/(\d{6}[_-]\d+)", str(url))
    return m.group(1).replace("-", "_") if m else ""

def load_catalog():
    global _catalog, _loaded
    if _catalog and time.time()-_loaded < CACHE_SECONDS: return _catalog
    response = session.get(CATALOG_URL, timeout=60)
    response.raise_for_status()
    graph, parsed = Graph(), False
    for fmt in ("xml", "turtle", "json-ld", "n3"):
        try:
            graph.parse(data=response.content, format=fmt); parsed=True; break
        except Exception: graph=Graph()
    if not parsed: raise ValueError("Le catalogue RDF n'a pas pu être lu.")
    indicators = {}
    for subject in graph.subjects():
        title = get_text(graph, subject, DCTERMS.title)
        if not title: continue
        description = get_text(graph, subject, DCTERMS.description)
        code = ""
        for dist in graph.objects(subject, DCAT.distribution):
            for pred in (DCAT.accessURL, DCAT.downloadURL):
                for url in graph.objects(dist, pred):
                    code = get_code(url)
                    if code: break
                if code: break
            if code: break
        if not code:
            p = parse_qs(urlparse(str(subject)).query)
            base, order = p.get("indicateur_id", [""])[0], p.get("ordre", [""])[0]
            if base.isdigit() and order.isdigit(): code=f"{base}_{order}"
        if code: indicators[code]={"id":code,"title":title,"name":title,"description":description}
    if not indicators: raise ValueError("Aucun identifiant n'a pu être extrait du catalogue.")
    _catalog=sorted(indicators.values(),key=lambda x:x["title"].casefold()); _loaded=time.time()
    return _catalog

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/health")
def health(): return jsonify({"status":"ok","application":"Explorateur IWEPS"})

@app.route("/api/indicators")
def indicators():
    q=request.args.get("q","").strip().casefold()
    try: catalog=load_catalog()
    except Exception:
        app.logger.exception("Catalogue IWEPS inaccessible")
        return jsonify({"indicators":[],"error":"Le catalogue IWEPS est temporairement inaccessible."}),502
    found=[x for x in catalog if not q or q in x["title"].casefold() or q in x["description"].casefold() or q in x["id"].casefold()]
    try: limit=max(1,min(int(request.args.get("limit","50")),100))
    except ValueError: limit=50
    return jsonify({"count":len(found),"indicators":found[:limit],"message":f"{len(found)} indicateur(s) trouvé(s). Liste limitée aux premiers résultats."})

@app.route("/api/data")
def data():
    indicator=request.args.get("indicator","").replace("-","_")
    fmt=request.args.get("format","json").lower()
    territory=request.args.get("territory","com").lower()
    period=request.args.get("period","all").lower()
    if not re.fullmatch(r"\d{6}_\d+",indicator): return jsonify({"error":"Identifiant invalide. Exemple : 831110_1."}),400
    if fmt not in ("json","csv"): return jsonify({"error":"Le format doit être json ou csv."}),400
    if period not in ("all","last"): return jsonify({"error":"La période doit être all ou last."}),400
    levels={"reg":"ins=3000","com":"com","arr":"arr","prov":"prov"}
    if territory not in levels: return jsonify({"error":"Territoire non reconnu."}),400
    url=f"{DATA_URL}/{fmt}/{indicator}/{levels[territory]}+period={period}"
    try:
        r=session.get(url,timeout=60)
        if r.status_code!=200:
            return jsonify({"error":"L'IWEPS ne fournit pas de données pour cet indicateur et ce territoire, ou le service est temporairement indisponible."}),502
        ct="text/csv; charset=utf-8" if fmt=="csv" else "application/json; charset=utf-8"
        return Response(r.content,status=200,content_type=ct)
    except requests.RequestException:
        app.logger.exception("Erreur API IWEPS")
        return jsonify({"error":"Impossible de contacter l'API IWEPS."}),502

if __name__=="__main__":
    import os
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
