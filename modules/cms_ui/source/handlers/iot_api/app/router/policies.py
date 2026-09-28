# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import uuid
from http import HTTPStatus
from typing import Annotated, Optional
from pydantic import UUID4
from sqlalchemy import select, insert, update, delete, func, distinct
from aws_lambda_powertools.event_handler.openapi.params import Query, Body
from aws_lambda_powertools.event_handler import Response, content_types
from utils import session, logger
from utils.models import (
    User,
    Policy,
    UserPolicyRelation,
)
from utils.ext import (
    apply_filters,
    apply_sort,
    SortSpec,
)
from app.schema import (
    GeneralResponse,
    PolicyItem,
    PolicyDocument,
    ListPolicies,
    PolicyFilterSpec,
    UserItem,
    ListUsers,
)
from app.resources import tracer, app


@app.post(
    "/policies/list",
    summary="List policies",
    description="List policies",
    response_description="A list of policies",
)
@tracer.capture_method
def list_policies(
    filter_specs: Annotated[
        Optional[list[PolicyFilterSpec]],
        Body(description="The filter specs."),
    ] = None,
    sort_specs: Annotated[
        Optional[list[SortSpec]], Body(description="The sort specs.")
    ] = None,
    page: Annotated[int, Body(ge=1)] = 1,
    size: Annotated[int, Body(le=1000)] = 10,
) -> Response[ListPolicies]:
    total_stmt = select(func.count()).select_from(Policy)
    total_stmt = apply_filters(statement=total_stmt, filter_specs=filter_specs)

    total = session.execute(total_stmt).scalar() or 0
    data = []

    if total > 0:
        subquery = (
            select(
                Policy.uid,
                func.count(UserPolicyRelation.uid).label("related_user_count"),
            )
            .select_from(Policy, UserPolicyRelation)
            .where(Policy.uid == UserPolicyRelation.policy_uid)
            .group_by(Policy.uid)
            .subquery()
        )
        list_stmt = (
            select(
                Policy.uid,
                Policy.name,
                Policy.description,
                Policy.document,
                Policy.created_at,
                Policy.updated_at,
                subquery.c.related_user_count,
            )
            .select_from(Policy)
            .join(
                target=subquery,
                onclause=(subquery.c.uid == Policy.uid),
                isouter=True,
            )
            .limit(size)
            .offset((page - 1) * size)
        )
        list_stmt = apply_filters(statement=list_stmt, filter_specs=filter_specs)
        list_stmt = apply_sort(statement=list_stmt, sort_specs=sort_specs)
        data = [
            PolicyItem(
                uid=uid,
                name=name,
                description=description,
                document=document,
                related_user_count=related_user_count,
                created_at=created_at,
                updated_at=updated_at,
            )
            for uid, name, description, document, created_at, updated_at, related_user_count in session.execute(
                list_stmt
            ).all()
        ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListPolicies(total=total, data=data),
    )


@app.post(
    "/policies/<policy_uid>",
    summary="Get policy by uid",
    description="Get policy information identified by the `uid`.",
    response_description="Policy details",
)
@tracer.capture_method
def get_policy(
    policy_uid: str,
) -> Response[PolicyItem | None]:
    stmt = select(Policy).where(Policy.uid == policy_uid)
    policy_item = session.execute(stmt).scalar()
    if not policy_item:
        return Response(
            status_code=HTTPStatus.NOT_FOUND,
            content_type=content_types.APPLICATION_JSON,
            body=None,
        )
    policy_item = PolicyItem.model_validate(policy_item, from_attributes=True)
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=policy_item,
    )


@app.post(
    "/policies",
    summary="Create a new policy",
    description="Create a new policy",
    response_description="status",
)
@tracer.capture_method
def create_policy(
    name: Annotated[str, Body(description="The name of policy.")],
    document: Annotated[PolicyDocument, Body(description="The document of policy.")],
    description: Annotated[
        str | None, Body(description="The description of policy.")
    ] = None,
) -> Response[GeneralResponse]:
    if session.execute(select(Policy).where(Policy.name == name)).scalar() is not None:
        return Response(
            status_code=HTTPStatus.CONFLICT,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(status="failure", message="Policy already exists"),
        )
    uid = uuid.uuid4()
    session.execute(
        insert(Policy).values(
            uid=uid,
            name=name,
            document=document.model_dump(),
            description=description,
        )
    )
    session.commit()
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(status="success", message=str(uid)),
    )


@app.put(
    "/policies/<policy_uid>",
    summary="Update a policy",
    description="Update a policy",
    response_description="status",
)
@tracer.capture_method
def update_policy(
    policy_uid: UUID4,
    document: Annotated[
        PolicyDocument | None, Body(description="The document of policy.")
    ] = None,
    description: Annotated[
        str | None, Body(description="The description of policy.")
    ] = None,
) -> Response[GeneralResponse]:
    stmt = update(Policy).where(Policy.uid == policy_uid)

    if document is not None:
        stmt = stmt.values(document=document.model_dump())

    if description is not None:
        stmt = stmt.values(description=description)

    session.execute(stmt)
    session.commit()
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(status="success", message=str(policy_uid)),
    )


@app.delete(
    "/policies/<policy_uid>",
    summary="Delete a policy",
    description="Delete a policy",
    response_description="status",
)
@tracer.capture_method
def delete_policy(
    policy_uid: UUID4,
) -> Response[GeneralResponse]:
    session.execute(
        delete(UserPolicyRelation).where(UserPolicyRelation.policy_uid == policy_uid)
    )
    session.execute(delete(Policy).where(Policy.uid == policy_uid))
    session.commit()

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(status="success"),
    )


@app.post(
    "/policies/<policy_uid>/users/list",
    summary="List users related by policy",
    description="List users related by policy",
    response_description="A list of users",
)
@tracer.capture_method
def list_users_by_policy(
    policy_uid: str,
    page: Annotated[int, Body(ge=1)] = 1,
    size: Annotated[int, Body(le=1000)] = 10,
) -> Response[ListUsers]:
    total_stmt = (
        select(func.count())
        .select_from(User, Policy, UserPolicyRelation)
        .where(
            User.uid == UserPolicyRelation.user_uid,
            UserPolicyRelation.policy_uid == Policy.uid,
            Policy.uid == policy_uid,
        )
    )
    total = session.execute(total_stmt).scalar() or 0
    data = []

    if total > 0:
        list_stmt = (
            select(
                User.uid,
                User.name,
                User.disconnect_after_in_seconds,
                User.refresh_after_in_seconds,
                User.status,
                User.created_at,
                User.updated_at,
            )
            .select_from(User, Policy, UserPolicyRelation)
            .where(
                User.uid == UserPolicyRelation.user_uid,
                UserPolicyRelation.policy_uid == Policy.uid,
                Policy.uid == policy_uid,
            )
            .limit(size)
            .offset((page - 1) * size)
            .order_by(UserPolicyRelation.created_at.desc())
        )
        data = [
            UserItem(
                uid=uid,
                name=name,
                status=status,
                disconnect_after_in_seconds=disconnect_after_in_seconds,
                refresh_after_in_seconds=refresh_after_in_seconds,
                created_at=created_at,
                updated_at=updated_at,
            )
            for uid, name, disconnect_after_in_seconds, refresh_after_in_seconds, status, created_at, updated_at in session.execute(
                list_stmt
            ).all()
        ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListUsers(total=total, data=data),
    )
