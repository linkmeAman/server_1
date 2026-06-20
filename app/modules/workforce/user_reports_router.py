from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Dict, Any
from pydantic import BaseModel

from app.core.database import get_central_db_session, get_main_db_session

router = APIRouter(prefix="/user-report", tags=["workforce-reports"])

class ReportPermissionItem(BaseModel):
    report_id: int
    module_id: int
    has_access: bool

class UpdateReportPermissionsRequest(BaseModel):
    employee_id: int
    branch_ids: List[int]
    report_permissions: List[ReportPermissionItem]

@router.get("/metadata")
async def get_metadata(
    main_db: AsyncSession = Depends(get_main_db_session),
    central_db: AsyncSession = Depends(get_central_db_session),
):
    # 1. Fetch Branches
    branches_query = text("SELECT id, branch FROM branch WHERE park = 0 AND id != 86")
    branches_result = await main_db.execute(branches_query)
    branches = [{"id": row.id, "name": row.branch} for row in branches_result]

    # 2. Fetch Employees (Users mapped to branches)
    users_query = text("""
        SELECT u.id as user_id, u.fname, u.lname, ub.bid
        FROM user u
        JOIN user_bid ub ON u.id = ub.user_id
        WHERE u.park = 0 AND u.inactive = 0 AND ub.bid != 86
    """)
    users_result = await central_db.execute(users_query)
    
    employees = {}
    for row in users_result:
        user_id = row.user_id
        if user_id not in employees:
            employees[user_id] = {
                "id": user_id,
                "name": f"{row.fname} {row.lname}".strip(),
                "bids": []
            }
        employees[user_id]["bids"].append(row.bid)

    # 3. Fetch Modules and Reports
    modules_query = text("SELECT id, name, parent_id FROM module WHERE park = 0")
    modules_result = await central_db.execute(modules_query)
    modules = [{"id": row.id, "name": row.name, "parent_id": row.parent_id} for row in modules_result]
    
    reports_query = text("SELECT id, module_id, name, title FROM report WHERE report = 1")
    reports_result = await main_db.execute(reports_query)
    reports = [{"id": row.id, "module_id": row.module_id, "name": row.name, "title": row.title} for row in reports_result]

    return {
        "success": True,
        "data": {
            "branches": branches,
            "employees": list(employees.values()),
            "modules": modules,
            "reports": reports
        }
    }

@router.get("/permissions")
async def get_permissions(
    employee_id: int,
    branch_ids: str,
    central_db: AsyncSession = Depends(get_central_db_session),
):
    bids = [int(b) for b in branch_ids.split(",") if b.strip().isdigit()]
    if not bids:
        return {"success": True, "data": []}
        
    placeholders = ",".join(str(b) for b in bids)
    
    query = text(f"""
        SELECT report_id, permission, bid 
        FROM user_report_permission 
        WHERE user_id = :user_id AND bid IN ({placeholders})
    """)
    result = await central_db.execute(query, {"user_id": employee_id})
    
    permissions = []
    for row in result:
        permissions.append({
            "report_id": row.report_id,
            "has_access": bool(row.permission),
            "bid": row.bid
        })
        
    return {
        "success": True,
        "data": permissions
    }

@router.post("/update")
async def update_permissions(
    request: UpdateReportPermissionsRequest,
    central_db: AsyncSession = Depends(get_central_db_session),
):
    if not request.branch_ids:
        raise HTTPException(status_code=400, detail="branch_ids cannot be empty")
        
    placeholders = ",".join(str(b) for b in request.branch_ids)
    
    delete_query = text(f"""
        DELETE FROM user_report_permission 
        WHERE user_id = :user_id AND bid IN ({placeholders})
    """)
    await central_db.execute(delete_query, {"user_id": request.employee_id})
    
    if request.report_permissions:
        insert_values = []
        params = {}
        idx = 0
        
        for p in request.report_permissions:
            if p.has_access:
                for bid in request.branch_ids:
                    insert_values.append(f"(:user_id, :report_{idx}, 1, :bid_{idx})")
                    params[f"report_{idx}"] = p.report_id
                    params[f"bid_{idx}"] = bid
                    idx += 1
                    
        if insert_values:
            params["user_id"] = request.employee_id
            insert_query = text(f"""
                INSERT INTO user_report_permission (user_id, report_id, permission, bid)
                VALUES {",".join(insert_values)}
            """)
            await central_db.execute(insert_query, params)
            
    await central_db.commit()
    
    return {
        "success": True,
        "message": "Report permissions updated successfully"
    }
