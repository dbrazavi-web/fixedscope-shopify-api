import os, json, requests
from datetime import datetime, timedelta, date
from functools import wraps
from flask import Flask, request, jsonify, abort

PROXY_API_KEY = os.environ.get("FIXEDSCOPE_PROXY_KEY")
API_VERSION = "2026-01"

def get_client_config(client):
    return {
        "store_url": os.environ.get(f"{client.upper()}_STORE_URL", ""),
        "client_id": os.environ.get(f"{client.upper()}_CLIENT_ID", ""),
        "client_secret": os.environ.get(f"{client.upper()}_CLIENT_SECRET", ""),
    }

_tokens = {}

def get_token(client):
    cached = _tokens.get(client)
    if cached and datetime.now() < cached["expires"]:
        return cached["token"]
    config = get_client_config(client)
    r = requests.post(f"https://{config['store_url']}/admin/oauth/access_token",
        data={"grant_type":"client_credentials","client_id":config["client_id"],"client_secret":config["client_secret"]},
        headers={"Content-Type":"application/x-www-form-urlencoded"}, timeout=30)
    data = r.json()
    _tokens[client] = {"token": data["access_token"], "expires": datetime.now() + timedelta(seconds=82800)}
    return data["access_token"]

def shopify_get(client, endpoint, params=None):
    config = get_client_config(client)
    token = get_token(client)
    r = requests.get(f"https://{config['store_url']}/admin/api/{API_VERSION}/{endpoint}",
        headers={"X-Shopify-Access-Token": token}, params=params or {}, timeout=30)
    return r.json() if r.status_code == 200 else {"error": r.text}

app = Flask(__name__)

def require_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.headers.get("X-FixedScope-Key","") != PROXY_API_KEY:
            abort(401)
        return f(*args, **kwargs)
    return decorated

@app.route("/health")
def health():
    return jsonify({"status":"ok","service":"fixedscope-shopify-proxy"})

@app.route("/api/shopify/<client>/shop")
@require_key
def shop_info(client):
    return jsonify(shopify_get(client, "shop.json"))

@app.route("/api/shopify/<client>/orders")
@require_key
def orders(client):
    params = {"status":"any","limit":250}
    d = request.args.get("date")
    if d:
        params["created_at_min"] = f"{d}T00:00:00"
        params["created_at_max"] = f"{d}T23:59:59"
    s, e = request.args.get("start"), request.args.get("end")
    if s and e:
        params["created_at_min"] = f"{s}T00:00:00"
        params["created_at_max"] = f"{e}T23:59:59"
    return jsonify(shopify_get(client, "orders.json", params))

@app.route("/api/shopify/<client>/products")
@require_key
def products(client):
    return jsonify(shopify_get(client, "products.json", {"limit":50}))

@app.route("/api/shopify/<client>/customers/count")
@require_key
def cust_count(client):
    return jsonify(shopify_get(client, "customers/count.json"))

@app.route("/api/shopify/<client>/orders/count")
@require_key
def ord_count(client):
    params = {"status":"any"}
    y = request.args.get("year", type=int)
    if y:
        params["created_at_min"] = f"{y}-01-01T00:00:00"
        params["created_at_max"] = f"{y}-12-31T23:59:59"
    return jsonify(shopify_get(client, "orders/count.json", params))

@app.route("/api/shopify/<client>/daily")
@require_key
def daily(client):
    d = request.args.get("date", date.today().isoformat())
    params = {"status":"any","limit":250,"created_at_min":f"{d}T00:00:00","created_at_max":f"{d}T23:59:59"}
    data = shopify_get(client, "orders.json", params)
    ords = data.get("orders", [])
    gross = sum(float(o.get("subtotal_price",0)) for o in ords)
    disc = sum(float(o.get("total_discounts",0)) for o in ords)
    ref = sum(float(o.get("subtotal_price",0)) for o in ords if o.get("financial_status")=="refunded")
    net = gross - ref - disc
    prods = {}
    units = 0
    for o in ords:
        for item in o.get("line_items",[]):
            n = item.get("title","?")
            q = item.get("quantity",0)
            r2 = float(item.get("price",0))*q
            units += q
            if n not in prods: prods[n]={"units":0,"revenue":0}
            prods[n]["units"]+=q
            prods[n]["revenue"]+=r2
    return jsonify({"date":d,"orders":len(ords),"gross":round(gross,2),"discounts":round(disc,2),
        "refunds":round(ref,2),"net":round(net,2),"units":units,"aov":round(net/len(ords),2) if ords else 0,
        "products":sorted([{"name":k,**v} for k,v in prods.items()],key=lambda x:x["revenue"],reverse=True)})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
