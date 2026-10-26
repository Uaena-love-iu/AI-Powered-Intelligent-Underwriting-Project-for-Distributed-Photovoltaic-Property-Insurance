"""Same-origin workbench UI; all data APIs use the main app's role checks."""

from html import escape

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.component_workbench_runtime import WORKBENCH_ROOT, runtime

API_BASE = "/api/v1/underwriting/component-model-workbench"
PAGE = "/component-model-workbench"


def install(app: FastAPI) -> None:
    app.mount(PAGE + "/static", StaticFiles(directory=WORKBENCH_ROOT / "web"))
    app.mount(API_BASE, runtime().app)

    @app.get(PAGE, include_in_schema=False)
    def redirect(request: Request):
        return RedirectResponse(request.scope.get("root_path", "") + PAGE + "/")

    @app.get(PAGE + "/", include_in_schema=False)
    def page(request: Request):
        prefix = escape(request.scope.get("root_path", "").rstrip("/"), quote=True)
        content = (WORKBENCH_ROOT / "web/index.html").read_text(encoding="utf-8")
        content = content.replace('"/static/', '"' + prefix + PAGE + '/static/')
        content = content.replace('<head>', '<head><meta name="component-api-base" content="'
                                  + prefix + API_BASE + '">', 1)
        content = content.replace('class="brand" href="/"',
                                  'class="brand" href="' + prefix + '/evaluation"')
        content = content.replace('测试工作区</div>', '测试工作区</div><a class="nav" href="'
                                  + prefix + '/evaluation">← 返回核保项目</a>')
        content = content.replace('<script src="' + prefix + PAGE + '/static/app.js',
                                  '<script src="' + prefix + '/workbench.js?v=component-replacement"></script><script src="' + prefix + PAGE + '/static/app.js')
        return HTMLResponse(content, headers={"Cache-Control": "no-store"})
