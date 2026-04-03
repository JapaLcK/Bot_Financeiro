# run_whatsapp.py
import os
from adapters.whatsapp.wa_webhook import app

if __name__ == "__main__":
    port = int(os.getenv("PORT") or "5001")
    app.run(host="0.0.0.0", port=port, debug=False)