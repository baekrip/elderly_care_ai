"""라우트 Blueprint 모음.

각 모듈이 자기 Blueprint에 라우트를 등록하고, app.py가 register_blueprints(app)로
한 번에 붙인다. Blueprint는 정의 시점에 app 객체가 필요 없어서
app.py <-> routes 순환 import가 생기지 않는다.
"""
from .health import health_bp
from .auth_routes import auth_bp
from .admin_users import admin_users_bp
from .admin_households import admin_households_bp
from .admin_patients import admin_patients_bp
from .admin_devices import admin_devices_bp
from .ingest import ingest_bp
from .clips import clips_bp
from .query import query_bp

__all__ = ["register_blueprints"]


ALL_BLUEPRINTS = (
    health_bp,
    auth_bp,
    admin_users_bp,
    admin_households_bp,
    admin_patients_bp,
    admin_devices_bp,
    ingest_bp,
    clips_bp,
    query_bp,
)


def register_blueprints(app) -> None:
    """모든 라우트를 app에 등록. URL 규칙은 각 모듈 안에 그대로 박혀 있다."""
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)
