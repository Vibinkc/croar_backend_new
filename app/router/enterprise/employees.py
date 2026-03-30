from typing import Annotated, List, Optional
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.models.enterprise.employee import Employee, Department
from app.schemas.enterprise.employees import (
    EmployeeOut, EmployeeCreate, EmployeeUpdate,
    DepartmentOut, DepartmentCreate, DepartmentUpdate
)
from app.services.enterprise.employee_service import employee_service

router = APIRouter(prefix="/employees", tags=["Enterprise Employees"])

# Department CRUD
@router.post("/departments", response_model=DepartmentOut, status_code=201)
async def create_department(
    request: DepartmentCreate,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    department = Department(**request.model_dump())
    session.add(department)
    await session.commit()
    await session.refresh(department)
    return department

@router.get("/departments", response_model=List[DepartmentOut])
async def list_departments(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    stmt = select(Department).where(Department.company_id != None) # Simple filter for now
    result = await session.execute(stmt)
    return result.scalars().all()

# Employee CRUD
@router.post("/", response_model=EmployeeOut, status_code=201)
async def create_employee(
    request: EmployeeCreate,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    employee = Employee(**request.model_dump())
    session.add(employee)
    await session.commit()
    await session.refresh(employee)
    return employee

@router.get("/", response_model=List[EmployeeOut])
async def list_employees(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    stmt = select(Employee).options(
        selectinload(Employee.department),
        selectinload(Employee.reporting_to)
    ).where(Employee.deleted_at == None)
    
    result = await session.execute(stmt)
    return result.scalars().all()

@router.get("/{id}", response_model=EmployeeOut)
async def get_employee(
    id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    stmt = select(Employee).options(
        selectinload(Employee.department),
        selectinload(Employee.reporting_to)
    ).where(Employee.id == id, Employee.deleted_at == None)
    
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
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    employee = await session.get(Employee, id)
    if not employee or employee.deleted_at:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(employee, key, value)
    
    employee.updated_at = datetime.now()
    await session.commit()
    await session.refresh(employee)
    return employee

@router.delete("/{id}", status_code=204)
async def delete_employee(
    id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    employee = await session.get(Employee, id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    employee.deleted_at = datetime.now()
    await session.commit()
    return

# Conversion
@router.post("/convert-candidate/{candidate_id}", response_model=EmployeeOut)
async def convert_candidate(
    candidate_id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    try:
        agent_name = f"{current_agent.first_name} {current_agent.last_name or ''}".strip()
        employee = await employee_service.convert_candidate_to_employee(session, candidate_id, agent_name)
        await session.commit()
        await session.refresh(employee)
        return employee
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
