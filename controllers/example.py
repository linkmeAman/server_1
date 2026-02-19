"""
Example controller demonstrating various API patterns
This shows different types of functions and parameter handling

Available endpoints:
- GET /py/example/hello - Simple hello world
- GET /py/example/echo/{message} - Echo a message
- POST /py/example/calculate - Perform calculations
- GET /py/example/users - List users (with pagination)
- GET /py/example/users/{id} - Get specific user
- POST /py/example/users - Create new user
"""
import logging
from typing import Optional, List, Dict, Any, Union
import time
import random

logger = logging.getLogger(__name__)

# Sample user data
SAMPLE_USERS = [
    {"id": 1, "name": "John Doe", "email": "john@example.com", "status": "active"},
    {"id": 2, "name": "Jane Smith", "email": "jane@example.com", "status": "active"}, 
    {"id": 3, "name": "Bob Johnson", "email": "bob@example.com", "status": "inactive"},
    {"id": 4, "name": "Alice Brown", "email": "alice@example.com", "status": "active"}
]


def hello(name: str = "World") -> Dict[str, Any]:
    """
    Simple hello world function
    GET /py/example/hello?name=John
    """
    logger.info(f"Hello called with name: {name}")
    return {
        "message": f"Hello, {name}!",
        "timestamp": time.time(),
        "controller": "example"
    }


def echo(message: str) -> Dict[str, Any]:
    """
    Echo a message from URL path
    GET /py/example/echo/your-message-here
    """
    logger.info(f"Echo called with message: {message}")
    return {
        "original_message": message,
        "echo": message.upper(),
        "length": len(message),
        "reversed": message[::-1]
    }


def calculate(
    operation: str,
    a: float,
    b: float
) -> Dict[str, Any]:
    """
    Perform mathematical calculations
    POST /py/example/calculate
    {
        "operation": "add",
        "a": 10.5,
        "b": 5.2
    }
    """
    logger.info(f"Calculate called: {operation}({a}, {b})")
    
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else None
    }
    
    if operation not in operations:
        return {
            "success": False,
            "error": "InvalidOperation",
            "message": f"Operation '{operation}' not supported",
            "supported_operations": list(operations.keys())
        }
    
    try:
        result = operations[operation](a, b)
        if result is None:
            return {
                "success": False,
                "error": "DivisionByZero",
                "message": "Cannot divide by zero"
            }
        
        return {
            "success": True,
            "operation": operation,
            "operands": {"a": a, "b": b},
            "result": result
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": "CalculationError",
            "message": str(e)
        }


def users(
    page: int = 1,
    per_page: int = 10,
    status: Optional[str] = None
) -> Dict[str, Any]:
    """
    List users with pagination and filtering
    GET /py/example/users?page=1&per_page=10&status=active
    """
    logger.info(f"Users list called - page: {page}, per_page: {per_page}, status: {status}")
    
    # Filter users by status if provided
    filtered_users = SAMPLE_USERS
    if status:
        filtered_users = [u for u in SAMPLE_USERS if u["status"] == status]
    
    # Calculate pagination
    total_count = len(filtered_users)
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    users_page = filtered_users[start_idx:end_idx]
    
    total_pages = (total_count + per_page - 1) // per_page
    
    return {
        "success": True,
        "data": users_page,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_count": total_count,
            "has_next": page < total_pages,
            "has_prev": page > 1
        },
        "filters": {"status": status}
    }


def user(id: int) -> Dict[str, Any]:
    """
    Get specific user by ID
    GET /py/example/user/123
    """
    logger.info(f"Getting user with ID: {id}")
    
    for user in SAMPLE_USERS:
        if user["id"] == id:
            return {
                "success": True,
                "data": user,
                "message": f"Found user with ID {id}"
            }
    
    return {
        "success": False,
        "error": "UserNotFound",
        "message": f"User with ID {id} not found"
    }


def create_user(
    name: str,
    email: str,
    status: str = "active"
) -> Dict[str, Any]:
    """
    Create a new user
    POST /py/example/create_user
    {
        "name": "New User",
        "email": "newuser@example.com",
        "status": "active"
    }
    """
    logger.info(f"Creating user: {name} ({email})")
    
    # Simulate ID generation
    new_id = max(u["id"] for u in SAMPLE_USERS) + 1 if SAMPLE_USERS else 1
    
    new_user = {
        "id": new_id,
        "name": name,
        "email": email,
        "status": status,
        "created_at": time.time()
    }
    
    # In a real app, you'd save to database here
    SAMPLE_USERS.append(new_user)
    
    return {
        "success": True,
        "data": new_user,
        "message": f"User '{name}' created successfully"
    }


def random_data(count: int = 5) -> Dict[str, Any]:
    """
    Generate random test data
    GET /py/example/random_data?count=10
    """
    logger.info(f"Generating {count} random data items")
    
    data = []
    for i in range(count):
        data.append({
            "id": i + 1,
            "random_number": random.randint(1, 100),
            "random_float": round(random.uniform(0, 1), 4),
            "timestamp": time.time() + i
        })
    
    return {
        "success": True,
        "data": data,
        "count": count,
        "generated_at": time.time()
    }


async def async_task(duration: float = 1.0) -> Dict[str, Any]:
    """
    Demonstrate async function handling
    POST /py/example/async_task {"duration": 2.5}
    """
    import asyncio
    
    logger.info(f"Starting async task for {duration} seconds")
    start_time = time.time()
    
    await asyncio.sleep(duration)
    
    end_time = time.time()
    actual_duration = end_time - start_time
    
    return {
        "success": True,
        "message": f"Async task completed",
        "requested_duration": duration,
        "actual_duration": round(actual_duration, 3),
        "start_time": start_time,
        "end_time": end_time
    }


def status() -> Dict[str, Any]:
    """
    Controller status and statistics
    GET /py/example/status
    """
    return {
        "controller": "example",
        "status": "healthy",
        "features": [
            "Basic functions",
            "Parameter handling",
            "Path parameters",
            "Query parameters",
            "JSON body parameters",
            "Pagination",
            "Filtering",
            "Async functions",
            "Error handling"
        ],
        "sample_data_counts": {
            "users": len(SAMPLE_USERS)
        },
        "timestamp": time.time()
    }