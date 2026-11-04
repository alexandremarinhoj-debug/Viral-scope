import os
import re
import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")
API_KEY = os.getenv("YOUTUBE_API_KEY")

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

@app.get("/")
def home():
    return send_from_directory(".", "index.html")

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "ViralScope API"})

def yt_get(endpoint, params):
    if not API_KEY:
        return None, (jsonify({
            "error": "A chave YOUTUBE_API_KEY não foi encontrada nas variáveis do serviço."
        }), 500)
    params["key"] = API_KEY
    try:
        response = requests.get(f"{YOUTUBE_API}/{endpoint}", params=params, timeout=20)
        data = response.json()
        if response.status_code != 200:
            message = data.get("error", {}).get("message", "Erro na YouTube Data API.")
            return None, (jsonify({"error": message}), response.status_code)
        return data, None
    except requests.RequestException:
        return None, (jsonify({"error": "Não foi possível conectar à API do YouTube."}), 502)

@app.get("/api/search")
def search_videos():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"error": "Digite pelo menos 2 caracteres para pesquisar."}), 400
    max_results = min(max(int(request.args.get("maxResults", 10)), 1), 25)
    data, error = yt_get("search", {
        "part": "snippet", "type": "video", "q": q,
        "maxResults": max_results, "order": request.args.get("order", "relevance")
    })
    if error:
        return error
    ids = [item["id"]["videoId"] for item in data.get("items", [])]
    stats = {}
    if ids:
        detail, detail_error = yt_get("videos", {
            "part": "statistics,contentDetails", "id": ",".join(ids)
        })
        if not detail_error:
            stats = {v["id"]: v.get("statistics", {}) for v in detail.get("items", [])}
    results = []
    for item in data.get("items", []):
        vid = item["id"]["videoId"]
        sn = item["snippet"]
        results.append({
            "videoId": vid,
            "title": sn.get("title"),
            "channelTitle": sn.get("channelTitle"),
            "channelId": sn.get("channelId"),
            "publishedAt": sn.get("publishedAt"),
            "thumbnail": sn.get("thumbnails", {}).get("high", {}).get("url"),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "viewCount": int(stats.get(vid, {}).get("viewCount", 0))
        })
    return jsonify({"query": q, "results": results})

@app.get("/api/channel")
def channel():
    value = request.args.get("id") or request.args.get("q", "")
    value = value.strip()
    if not value:
        return jsonify({"error": "Informe o ID ou nome/handle do canal."}), 400
    params = {"part": "snippet,statistics,contentDetails", "maxResults": 1}
    if value.startswith("UC") and len(value) > 20:
        params["id"] = value
    else:
        params["forHandle"] = value.lstrip("@")
    data, error = yt_get("channels", params)
    if error:
        return error
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "Canal não encontrado."}), 404
    item = items[0]
    sn, st = item.get("snippet", {}), item.get("statistics", {})
    return jsonify({
        "channelId": item.get("id"),
        "title": sn.get("title"),
        "description": sn.get("description"),
        "thumbnail": sn.get("thumbnails", {}).get("high", {}).get("url"),
        "publishedAt": sn.get("publishedAt"),
        "subscriberCount": int(st.get("subscriberCount", 0)),
        "viewCount": int(st.get("viewCount", 0)),
        "videoCount": int(st.get("videoCount", 0)),
        "hiddenSubscriberCount": st.get("hiddenSubscriberCount", False)
    })

@app.get("/api/keywords")
def keywords():
    # YouTube Data API does not expose real search volume or keyword difficulty.
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"error": "Digite uma palavra-chave com pelo menos 2 caracteres."}), 400
    data, error = yt_get("search", {
        "part": "snippet", "type": "video", "q": q, "maxResults": 10,
        "order": "viewCount"
    })
    if error:
        return error
    titles = [x.get("snippet", {}).get("title", "") for x in data.get("items", [])]
    tokens = {}
    for title in titles:
        for word in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", title.lower()):
            if word not in q.lower():
                tokens[word] = tokens.get(word, 0) + 1
    related = sorted(tokens.items(), key=lambda x: (-x[1], x[0]))[:12]
    return jsonify({
        "keyword": q,
        "relatedTerms": [{"term": w, "foundInTitles": n} for w, n in related],
        "sampleTitles": titles,
        "notice": "Termos extraídos dos títulos dos vídeos encontrados; não representam volume real de buscas."
    })


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

@app.post("/api/mentor")
def mentor():
    """AI Channel Mentor powered by Gemini; API key remains server-side."""
    if not GEMINI_API_KEY:
        return jsonify({"error": "A variável GEMINI_API_KEY não foi configurada no servidor."}), 503
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    context = str(body.get("context", "")).strip()
    if len(message) < 3:
        return jsonify({"error": "Escreva uma pergunta com pelo menos 3 caracteres."}), 400
    if len(message) > 5000 or len(context) > 8000:
        return jsonify({"error": "A mensagem ou o contexto ultrapassou o limite permitido."}), 413

    system_prompt = (
        "Você é o AI Channel Mentor do ViralScope, um consultor experiente de estratégia para YouTube. "
        "Responda em português brasileiro, com clareza, objetividade e recomendações práticas. "
        "Diferencie fatos fornecidos, hipóteses e estimativas. Nunca invente métricas, dados do YouTube, "
        "CTR, retenção, impressões, volume de busca ou resultados. Dados públicos de concorrentes não "
        "revelam métricas privadas. Quando faltarem dados, diga o que falta. Priorize decisões testáveis, "
        "títulos honestos, thumbnails coerentes e retenção real. Não prometa viralização. "
        "Considere o contexto do canal fornecido, mas não afirme que possui acesso à conta do usuário."
    )
    prompt = system_prompt + "\n\nCONTEXTO DO CANAL (informado pelo usuário):\n" + (context or "Não fornecido.") + "\n\nPERGUNTA: " + message
    try:
        response = requests.post(
            GEMINI_URL.format(model=GEMINI_MODEL),
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.65, "maxOutputTokens": 1400}
            },
            timeout=60
        )
        data = response.json()
        if response.status_code != 200:
            detail = data.get("error", {}).get("message", "Falha ao consultar o Gemini.")
            code = 429 if response.status_code == 429 else 502 if response.status_code >= 500 else 400
            return jsonify({"error": detail}), code
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        answer = "\n".join(p.get("text", "") for p in parts if p.get("text"))
        if not answer:
            return jsonify({"error": "O Gemini não retornou uma resposta. Tente novamente."}), 502
        return jsonify({"answer": answer, "model": GEMINI_MODEL})
    except requests.Timeout:
        return jsonify({"error": "O Gemini demorou demais para responder. Tente novamente."}), 504
    except requests.RequestException:
        return jsonify({"error": "Não foi possível conectar ao serviço Gemini."}), 502
    except (ValueError, KeyError, IndexError, TypeError):
        return jsonify({"error": "Resposta inesperada do Gemini. Tente novamente."}), 502

@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Rota não encontrada."}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
