from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.employee import Department, Employee
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.employees import (
    DepartmentCreate,
    DepartmentOut,
    EmployeeCreate,
    EmployeeOut,
    EmployeeUpdate,
)
from app.services.enterprise.employee_service import employee_service

router = APIRouter(prefix="/employees", tags=["Enterprise Employees"])


# Department CRUD
@router.post("/departments", response_model=DepartmentOut, status_code=201)
async def create_department(
    request: DepartmentCreate,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.create))
    ],
) -> Department:
    company_id = getattr(current_user, "company_id", None)
    # Check for duplicate name in the same company
    existing_stmt = select(Department).where(
        Department.name == request.name, Department.company_id == company_id
    )
    existing = await session.execute(existing_stmt)
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Department with name '{request.name}' already exists.",
        )

    data = request.model_dump()
    data["company_id"] = company_id
    department = Department(**data)
    session.add(department)
    await session.commit()
    await session.refresh(department)
    return department


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> list[Department]:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Department).where(Department.company_id == company_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# Employee CRUD
@router.post("/", response_model=EmployeeOut, status_code=201)
async def create_employee(
    request: EmployeeCreate,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.create))
    ],
) -> Employee:
    # Check for duplicate email (Global uniqueness as per schema)
    email_stmt = select(Employee).where(Employee.email == request.email)
    existing_email = await session.execute(email_stmt)
    if existing_email.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Employee with email '{request.email}' already exists.",
        )

    # Check for duplicate employee_id (Global uniqueness as per schema)
    id_stmt = select(Employee).where(Employee.employee_id == request.employee_id)
    existing_id = await session.execute(id_stmt)
    if existing_id.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Employee with ID '{request.employee_id}' already exists.",
        )

    data = request.model_dump()
    data["company_id"] = getattr(current_user, "company_id", None)
    employee = Employee(**data)
    session.add(employee)
    await session.commit()

    # Eager load relationships for the response model to avoid MissingGreenlet
    stmt = (
        select(Employee)
        .options(selectinload(Employee.department), selectinload(Employee.reporting_to))
        .where(Employee.id == employee.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.get("/", response_model=list[EmployeeOut])
async def list_employees(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
) -> list[Employee]:
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(Employee)
        .options(selectinload(Employee.department), selectinload(Employee.reporting_to))
        .where(Employee.company_id == company_id, Employee.deleted_at is None)
    )

    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{id}", response_model=EmployeeOut)
async def get_employee(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
) -> Employee:
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(Employee)
        .options(selectinload(Employee.department), selectinload(Employee.reporting_to))
        .where(Employee.id == id, Employee.company_id == company_id, Employee.deleted_at is None)
    )

    result = await session.execute(stmt)
    employee = result.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@router.patch("/{id}", response_model=EmployeeOut)
async def update_employee(
    id: UUID,
    request: EmployeeUpdate,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> Employee:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Employee).where(
        Employee.id == id, Employee.company_id == company_id, Employee.deleted_at is None
    )
    res = await session.execute(stmt)
    employee = res.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(employee, key, value)

    employee.updated_at = cast("Any", datetime.now())
    await session.commit()

    # Eager load relationships for the response model
    stmt_reload = (
        select(Employee)
        .options(selectinload(Employee.department), selectinload(Employee.reporting_to))
        .where(Employee.id == employee.id)
    )
    result_reload = await session.execute(stmt_reload)
    return result_reload.scalar_one()


@router.delete("/{id}", status_code=204)
async def delete_employee(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.delete))
    ],
) -> None:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Employee).where(Employee.id == id, Employee.company_id == company_id)
    res = await session.execute(stmt)
    employee = res.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    employee.deleted_at = cast("Any", datetime.now())
    await session.commit()
    return


# Conversion
@router.post("/convert-candidate/{candidate_id}", response_model=EmployeeOut)
async def convert_candidate(
    candidate_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.moderate))
    ],
) -> Employee:
    try:
        first_name = getattr(current_user, "first_name", "")
        last_name = getattr(current_user, "last_name", "")
        agent_name = f"{first_name} {last_name}".strip() or "System"

        employee = await employee_service.convert_candidate_to_employee(session, candidate_id, agent_name)
        await session.commit()

        # Eager load relationships for the response model
        stmt = (
            select(Employee)
            .options(selectinload(Employee.department), selectinload(Employee.reporting_to))
            .where(Employee.id == employee.id)
        )
        result = await session.execute(stmt)
        return result.scalar_one()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {e!s}") from e
