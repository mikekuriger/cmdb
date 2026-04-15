from flask import Flask
from config import Config
from db import close_db
from auth import login_manager
from views import web_bp
from api import api_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    login_manager.init_app(app)
    app.teardown_appcontext(close_db)

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
