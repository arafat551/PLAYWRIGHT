import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sdet_app import database  # noqa: E402


def main():
    database.init_db()
    from sdet_app.app import app
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=True, port=port)


if __name__ == "__main__":
    main()
