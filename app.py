
import os
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# Adresse de l'API de données de l'IWEPS
IWEPS_API = "https://walstat.iweps.be"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "application": "Explorateur IWEPS"})


@app.route("/api/indicators")
def indicators():
    """Recherche des indicateurs dans l'API IWEPS."""
    query = request.args.get("q", "").strip()

    # Le catalogue sera connecté à l'API réelle après vérification
    # de ses routes et de son format de réponse.
    return jsonify({
        "query": query,
        "indicators": [],
        "message": (
            "Le catalogue IWEPS doit encore être connecté. "
            "Cette route est prête à être complétée."
        )
    })


@app.route("/api/data")
def data():
    """Point d'entrée pour les futures requêtes de données."""
    indicator = request.args.get("indicator", "").strip()

    if not indicator:
        return jsonify({
            "error": "Veuillez préciser un identifiant d'indicateur."
        }), 400

    return jsonify({
        "indicator": indicator,
        "message": "La connexion aux données sera ajoutée après validation de l'API."
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
