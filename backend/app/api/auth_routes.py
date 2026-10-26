from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.security import (
    AuthRepository,
    LastActiveAdmin,
    UserAlreadyExists,
    UserNotFound,
    auth_enabled,
)

from .schemas import LoginRequest, UserCreateRequest, UserPasswordResetRequest

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.get("/config")
def get_auth_config() -> dict[str, object]:
    """Expose whether the UI should show a login form, never secret values."""

    return {
        "enabled": auth_enabled(),
        "roles": ["viewer", "underwriter", "admin"],
    }


@router.post("/login")
def login(request: LoginRequest) -> dict[str, object]:
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Authentication is disabled",
        )
    repository = AuthRepository()
    user = repository.authenticate(
        username=request.username,
        password=request.password,
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token, expires_at = repository.issue_session(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": {"username": user.username, "role": user.role},
    }


@router.get("/me")
def get_current_user(request: Request) -> dict[str, str]:
    user = getattr(request.state, "auth_user", None)
    if user is None:
        return {"username": "local-demo", "role": "admin"}
    return {"username": user.username, "role": user.role}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() == "bearer" and token.strip():
            AuthRepository().revoke_session(token.strip())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users")
def list_users() -> list[dict[str, object]]:
    return AuthRepository().list_users()


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(request: UserCreateRequest) -> dict[str, str]:
    try:
        user = AuthRepository().create_user(
            username=request.username,
            password=request.password,
            role=request.role,
        )
    except UserAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return {"username": user.username, "role": user.role}


@router.post("/users/{username}/deactivate")
def deactivate_user(
    username: str,
    request: Request,
) -> dict[str, str]:
    current_user = getattr(request.state, "auth_user", None)
    if current_user is not None and current_user.username == username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own active session account",
        )
    try:
        deactivated = AuthRepository().deactivate_user(username)
    except LastActiveAdmin as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active administrator cannot be deactivated",
        ) from exc
    if not deactivated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"username": username, "status": "deactivated"}


@router.put("/users/{username}/password")
def reset_user_password(
    username: str,
    request: UserPasswordResetRequest,
) -> dict[str, str]:
    try:
        AuthRepository().reset_password(
            username=username,
            password=request.password,
        )
    except UserNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return {"username": username, "status": "password_reset; sessions_revoked"}
