# Controllers package
#
# Dual-mode routing is enabled:
# 1) Legacy dynamic routing (fallback):
#    Plain functions remain callable at /{controller}/{function}/{item_id?}
# 2) New explicit routing (preferred):
#    Add modules with `router = APIRouter(...)` and @router.<method> decorators.
#
# All new endpoints should use APIRouter-based modules.
# Converted legacy-standard routes are available under controllers/api/*.
