# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import uuid
import hashlib
from http import HTTPStatus
from typing import Annotated, Optional, Literal
from pydantic import UUID4
from sqlalchemy import select, insert, update, delete, func, and_, null
from sqlalchemy.exc import SQLAlchemyError
from aws_lambda_powertools.event_handler import Response, content_types
from aws_lambda_powertools.event_handler.openapi.params import Query, Body
from utils import session, logger
from utils.models import User, Policy, UserPolicyRelation, UserStatus
from utils.ext import (
    apply_filters,
    apply_sort,
    SortSpec,
)
from app.schema import (
    GeneralResponse,
    PolicyFilterSpec,
    UserPoliciesRelItem,
    ListUserPoliciesRel,
    UserItem,
    ListUsers,
)
from app.resources import tracer, app


@app.post(
    "/users/list",
    summary="List users",
    description="List users",
    response_description="A list of users",
)
@tracer.capture_method
def list_users(
    filter_specs: Annotated[
        Optional[list[PolicyFilterSpec]],
        Body(description="The filter specs."),
    ] = None,
    sort_specs: Annotated[
        Optional[list[SortSpec]], Body(description="The sort specs.")
    ] = None,
    page: Annotated[int, Body(ge=1)] = 1,
    size: Annotated[int, Body(le=1000)] = 10,
) -> Response[ListUsers]:
    total_stmt = select(func.count()).select_from(User)
    total_stmt = apply_filters(statement=total_stmt, filter_specs=filter_specs)

    total = session.execute(total_stmt).scalar() or 0
    data = []

    if total > 0:
        list_stmt = select(User).limit(size).offset((page - 1) * size)
        list_stmt = apply_filters(statement=list_stmt, filter_specs=filter_specs)
        list_stmt = apply_sort(statement=list_stmt, sort_specs=sort_specs)
        data = [
            UserItem(
                uid=x.uid,
                name=x.name,
                status=x.status,
                disconnect_after_in_seconds=x.disconnect_after_in_seconds,
                refresh_after_in_seconds=x.refresh_after_in_seconds,
                created_at=x.created_at,
                updated_at=x.updated_at,
            )
            for x in session.execute(list_stmt).scalars()
        ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListUsers(total=total, data=data),
    )


@app.post(
    "/users",
    summary="Create a new user",
    description="Create a new user",
    response_description="status",
)
@tracer.capture_method
def create_user(
    name: Annotated[str, Body(description="The name of user.")],
    password: Annotated[str, Body(description="The password of user.")],
    status: Annotated[
        UserStatus, Body(description="The status of user.")
    ] = UserStatus.ACTIVE,
    disconnect_after_in_seconds: Annotated[
        int,
        Body(
            description="An integer that specifies the maximum duration (in seconds) of the connection to the AWS IoT Core gateway."
        ),
    ] = 86400,
    refresh_after_in_seconds: Annotated[
        int,
        Body(
            description="An integer that specifies the interval between policy refreshes."
        ),
    ] = 3600,
) -> Response[GeneralResponse]:
    if session.scalar(select(User).where(User.name == name)):
        return Response(
            status_code=HTTPStatus.CONFLICT,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(
                status="failure", message=f"User: {name} already exists"
            ),
        )

    uid = uuid.uuid4()
    salt = os.urandom(16).hex()
    salted_password = f"{salt}{password}"
    hashed_password = hashlib.sha256(salted_password.encode("utf-8")).hexdigest()
    session.execute(
        insert(User).values(
            uid=uid,
            name=name,
            password=hashed_password,
            salt=salt,
            status=status,
            disconnect_after_in_seconds=disconnect_after_in_seconds,
            refresh_after_in_seconds=refresh_after_in_seconds,
        )
    )
    session.commit()
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(message=str(uid)),
    )


@app.put(
    "/users/<user_uid>",
    summary="Update a user",
    description="Update a user",
    response_description="status",
)
@tracer.capture_method
def update_user(
    user_uid: UUID4,
    password: Annotated[str | None, Body(description="The password of user.")] = None,
    disconnect_after_in_seconds: Annotated[
        int | None,
        Body(
            description="An integer that specifies the maximum duration (in seconds) of the connection to the AWS IoT Core gateway."
        ),
    ] = None,
    refresh_after_in_seconds: Annotated[
        int | None,
        Body(
            description="An integer that specifies the interval between policy refreshes."
        ),
    ] = None,
    status: Annotated[
        UserStatus | None, Body(description="The status of user.")
    ] = None,
) -> Response[GeneralResponse]:
    need_update = False
    stmt = update(User).where(User.uid == user_uid)

    if password is not None:
        salt = session.scalar(select(User.salt).where(User.uid == user_uid))
        salted_password = f"{salt}{password}"
        hashed_password = hashlib.sha256(salted_password.encode("utf-8")).hexdigest()
        stmt = stmt.values(password=hashed_password)
        need_update = True

    if status is not None:
        stmt = stmt.values(status=status)
        need_update = True

    if disconnect_after_in_seconds is not None:
        stmt = stmt.values(disconnect_after_in_seconds=disconnect_after_in_seconds)
        need_update = True

    if refresh_after_in_seconds is not None:
        stmt = stmt.values(refresh_after_in_seconds=refresh_after_in_seconds)
        need_update = True

    if need_update is True:
        session.execute(stmt)
        session.commit()
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(),
    )


@app.delete(
    "/users/<user_uid>",
    summary="Delete a user",
    description="Delete a user",
    response_description="status",
)
@tracer.capture_method
def delete_user(
    user_uid: UUID4,
) -> Response[GeneralResponse]:
    session.execute(
        delete(UserPolicyRelation).where(UserPolicyRelation.user_uid == user_uid)
    )
    session.execute(delete(User).where(User.uid == user_uid))
    session.commit()
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(),
    )


@app.post(
    "/users/<user_uid>/policies/list",
    summary="List users",
    description="List users",
    response_description="A list of users",
)
@tracer.capture_method
def list_user_policies(
    user_uid: UUID4,
    status: Annotated[Literal["attached", "unattached"], Body()] = "attached",
    page: Annotated[int, Body(ge=1)] = 1,
    size: Annotated[int, Body(le=1000)] = 10,
) -> Response[ListUserPoliciesRel]:

    subquery = select(UserPolicyRelation.policy_uid).where(
        UserPolicyRelation.user_uid == user_uid
    )

    where_clause = Policy.uid.in_(subquery)
    if status == "unattached":
        where_clause = Policy.uid.not_in(subquery)

    total_stmt = select(func.count()).select_from(Policy)
    total_stmt = total_stmt.where(where_clause)

    total = session.execute(total_stmt).scalar() or 0
    data = []

    if total > 0:
        if status == "unattached":
            list_stmt = (
                select(
                    null().label("uid"),
                    null().label("user_uid"),
                    Policy.uid,
                    Policy.name,
                    Policy.description,
                    Policy.document,
                    Policy.created_at,
                    Policy.updated_at,
                )
                .select_from(Policy)
                .where(where_clause)
                .order_by(Policy.created_at.desc())
                .limit(size)
                .offset((page - 1) * size)
            )
        else:
            list_stmt = (
                select(
                    UserPolicyRelation.uid,
                    UserPolicyRelation.user_uid,
                    UserPolicyRelation.policy_uid,
                    Policy.name,
                    Policy.description,
                    Policy.document,
                    UserPolicyRelation.created_at,
                    UserPolicyRelation.updated_at,
                )
                .select_from(UserPolicyRelation, Policy)
                .where(
                    UserPolicyRelation.user_uid == user_uid,
                    UserPolicyRelation.policy_uid == Policy.uid,
                )
                .order_by(UserPolicyRelation.created_at.desc())
                .limit(size)
                .offset((page - 1) * size)
            )

        data = [
            UserPoliciesRelItem(
                uid=uid,
                user_uid=user_uid,
                policy_uid=policy_uid,
                name=name,
                description=description,
                document=document,
                created_at=created_at,
                updated_at=updated_at,
            )
            for uid, user_uid, policy_uid, name, description, document, created_at, updated_at in session.execute(
                list_stmt
            ).all()
        ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListUserPoliciesRel(total=total, data=data),
    )


@app.post(
    "/users/<user_uid>",
    summary="Get user by uid",
    description="Get user information identified by the `uid`.",
    response_description="User details",
)
@tracer.capture_method
def get_user(
    user_uid: str,
) -> Response[UserItem | None]:
    stmt = select(User).where(User.uid == user_uid)
    user_item = session.execute(stmt).scalar()
    if not user_item:
        return Response(
            status_code=HTTPStatus.NOT_FOUND,
            content_type=content_types.APPLICATION_JSON,
            body=None,
        )
    user_item = UserItem.model_validate(user_item, from_attributes=True)
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=user_item,
    )


@app.post(
    "/users/<user_uid>/policies/<policy_uid>",
    summary="Establish a relationship between user and policy",
    description="Establish a relationship between user and policy",
    response_description="status",
)
@tracer.capture_method
def create_user_policy_rel(
    user_uid: UUID4,
    policy_uid: UUID4,
) -> Response[GeneralResponse]:
    if session.scalar(
        select(UserPolicyRelation).where(
            UserPolicyRelation.user_uid == user_uid,
            UserPolicyRelation.policy_uid == policy_uid,
        )
    ):
        return Response(
            status_code=HTTPStatus.CONFLICT,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(
                status="failure", message=f"User policy relation already exists"
            ),
        )

    uid = uuid.uuid4()
    session.execute(
        insert(UserPolicyRelation).values(
            uid=uid,
            user_uid=user_uid,
            policy_uid=policy_uid,
        )
    )
    session.commit()
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(message=str(uid)),
    )


@app.delete(
    "/users/<user_uid>/policies/<policy_uid>",
    summary="Delete a relationship between user and policy",
    description="Delete a relationship between user and policy",
    response_description="status",
)
@tracer.capture_method
def delete_user_policy_rel(
    user_uid: UUID4,
    policy_uid: UUID4,
) -> Response[GeneralResponse]:
    session.execute(
        delete(UserPolicyRelation).where(
            UserPolicyRelation.policy_uid == policy_uid,
            UserPolicyRelation.user_uid == user_uid,
        )
    )
    session.commit()
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(),
    )
