from flask import Flask, request, jsonify, render_template
import requests
import json
from datetime import datetime
import uuid
import os
import logging

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder='templates')

quotes = []  # In-memory store
conversations = {}

# Env from app.yaml or local
TOKEN = os.getenv('access_token')
HOST = os.getenv('server_hostname')
SERVING_ENDPOINT_NAME = os.getenv('SERVING_ENDPOINT')
ENDPOINT = None
if HOST and SERVING_ENDPOINT_NAME:
    ENDPOINT = "https://" + HOST + "/serving-endpoints/" + SERVING_ENDPOINT_NAME + "/invocations"

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/get_quotes', methods=['GET'])
def get_quotes():
    return jsonify(quotes)

@app.route('/new_conversation', methods=['POST'])
def new_conversation():
    conv_id = str(uuid.uuid4())
    conversations[conv_id] = []
    return jsonify({'conversation_id': conv_id})

@app.route('/send_message', methods=['POST'])
def send_message():
    data = request.json
    message = data['message']
    conv_id = data['conversation_id']

    if conv_id not in conversations:
        return jsonify({'error': 'Invalid conv_id'}), 400

    user_msg = {"role": "user", "content": message}
    current_history = conversations[conv_id] + [user_msg]

    payload = {
        "input": current_history,
        "custom_inputs": {}
    }
    headers = {
        "Content-Type": "application/json"
    }
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    else:
        return jsonify({'error': 'Missing access_token env var'}), 500

    try:
        if not ENDPOINT:
            return jsonify({'error': 'Missing endpoint config: Check server_hostname and SERVING_ENDPOINT env vars'}), 500

        logging.info("Calling " + ENDPOINT)
        resp = requests.post(ENDPOINT, headers=headers, json=payload, timeout=60)
        logging.info("Status: " + str(resp.status_code) + ", Body: " + resp.text[:500])

        if resp.status_code in [401, 403]:
            return jsonify({'error': 'Auth fail. Check token or permissions. Status: ' + str(resp.status_code)}), 401

        if resp.status_code != 200:
            return jsonify({'error': 'Endpoint error: Status ' + str(resp.status_code) + ' - ' + resp.text[:200]}), resp.status_code

        # Check if response is HTML (error page)
        if resp.text.strip().startswith('<'):
            return jsonify({'error': 'Received HTML error from server (likely invalid endpoint or config). Raw: ' + resp.text[:200]}), 500

        resp.raise_for_status()
        response_data = resp.json()  # Ab safe, JSON hoga

        bot_content = ""
        outputs = response_data.get('output', []) or response_data.get('choices', []) or []
        for item in outputs:
            if isinstance(item, dict):
                text_val = item.get('text') or item.get('content') or ""
                if isinstance(text_val, (list, dict)): text_val = json.dumps(text_val)
                bot_content += str(text_val)
                delta = item.get('delta', {}) or item.get('message', {})
                delta_content = delta.get('content') or delta.get('text') or ""
                if isinstance(delta_content, (list, dict)): delta_content = json.dumps(delta_content)
                bot_content += str(delta_content)
                if item.get('type') == 'function_call':
                    bot_content += " [Tool: " + str(item.get('content')) + "]"
            elif isinstance(item, (str, list)):
                bot_content += str(item)

        if not bot_content.strip():
            bot_content = "Empty from agent. Raw: " + json.dumps(response_data)[:500]

        conversations[conv_id].append(user_msg)
        conversations[conv_id].append({"role": "assistant", "content": bot_content})

        # Parse JSON from bot_content if present
        parsed = None
        try:
            if "{" in bot_content and "}" in bot_content:
                json_start = bot_content.rfind('{')
                json_end = bot_content.rfind('}') + 1
                json_str = bot_content[json_start:json_end]
                parsed = json.loads(json_str)
        except Exception as e:
            logging.error("Parse error: " + str(e))

        if parsed:
            quote_data = parsed.get("quote_data") or parsed
            normalized = {
                'quote_id': str(uuid.uuid4()),
                'created_at': datetime.now().isoformat(),
                'account_id': quote_data.get('AccountId', 'unknown'),
                'account_name': quote_data.get('AccountName', 'Unknown'),
                'product_name': quote_data.get('ProductName', 'Unknown'),
                'quantity': quote_data.get('Quantity', 0),
                'unit_price': quote_data.get('UnitPrice', 0.0),
                'partner_discount': quote_data.get('PartnerDiscount', 0.0),
                'regular_total': quote_data.get('RegularTotal', 0.0),
                'discounted_total': quote_data.get('DiscountedTotal', 0.0),
                'customer_type': quote_data.get('customer_type', 'Existing'),
                'pricebook': quote_data.get('selected_pricebook', 'Standard')
            }
            quotes.insert(0, normalized)  # Add to list for UI

        return jsonify({'response': bot_content})

    except requests.exceptions.RequestException as e:
        error_msg = "Network error: " + str(e)
        logging.error(error_msg)
        return jsonify({'error': error_msg}), 500
    except ValueError as e:  # JSON decode fail
        error_msg = "Invalid JSON from endpoint: " + str(e) + " Raw: " + resp.text[:200] if 'resp' in locals() else ""
        logging.error(error_msg)
        return jsonify({'error': error_msg}), 500
    except Exception as e:
        error_msg = "Unexpected error: " + str(e)
        logging.error(error_msg)
        return jsonify({'error': error_msg}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)