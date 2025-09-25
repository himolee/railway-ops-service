#!/usr/bin/env python3
"""
Railway Operations Microservice
Handles all Railway API operations for deployment automation
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
import requests
import json
import os
import logging
from datetime import datetime
import secrets

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Railway Operations Service",
    description="Microservice for Railway API operations",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"
RAILWAY_TOKEN = os.getenv("RAILWAY_TOKEN", "98d6ccf0-3cc8-4716-b5b5-4c33cce9d0dd")

# Pydantic models
class ServiceCreateRequest(BaseModel):
    project_id: str = Field(..., description="Railway project ID")
    repo: str = Field(..., description="GitHub repository (owner/repo)")
    service_name: Optional[str] = Field(None, description="Custom service name")
    root_directory: Optional[str] = Field(None, description="Root directory for deployment")

class VariableSetRequest(BaseModel):
    project_id: str = Field(..., description="Railway project ID")
    service_id: str = Field(..., description="Railway service ID")
    environment_id: Optional[str] = Field(None, description="Environment ID (defaults to production)")
    variables: Dict[str, str] = Field(..., description="Environment variables to set")

class DeploymentTriggerRequest(BaseModel):
    project_id: str = Field(..., description="Railway project ID")
    service_id: str = Field(..., description="Railway service ID")
    environment_id: Optional[str] = Field(None, description="Environment ID (optional, defaults to production)")

class ServiceResponse(BaseModel):
    id: str
    name: str
    created_at: str
    status: str = "created"

class VariableResponse(BaseModel):
    success: bool
    variables_set: List[str]
    errors: List[str] = []

# Railway API client
class RailwayClient:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def execute_query(self, query: str, variables: Dict = None) -> Dict:
        """Execute GraphQL query against Railway API"""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        
        try:
            response = requests.post(RAILWAY_API_URL, headers=self.headers, json=payload)
            response.raise_for_status()
            
            result = response.json()
            
            if "errors" in result:
                logger.error(f"GraphQL errors: {result['errors']}")
                raise HTTPException(status_code=400, detail=result["errors"])
            
            return result.get("data", {})
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Railway API request failed: {e}")
            raise HTTPException(status_code=500, detail=f"Railway API error: {str(e)}")

# Dependency injection
def get_railway_client() -> RailwayClient:
    return RailwayClient(RAILWAY_TOKEN)

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "Railway Operations Service",
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.2",
        "code_update": "deployment_trigger_fixed_with_environment_id_FORCE_REBUILD",
        "last_updated": "2025-09-25T04:47:30Z",
        "deployment_fix": "environment_id_parameter_added"
    }

@app.post("/services/create", response_model=ServiceResponse)
async def create_service(
    request: ServiceCreateRequest,
    client: RailwayClient = Depends(get_railway_client)
):
    """Create a new Railway service with GitHub repository"""
    
    logger.info(f"Creating service for repo: {request.repo}")
    
    # GraphQL mutation to create service
    mutation = """
    mutation serviceCreate($input: ServiceCreateInput!) {
      serviceCreate(input: $input) {
        id
        name
        createdAt
      }
    }
    """
    
    # Prepare input
    service_input = {
        "projectId": request.project_id,
        "source": {"repo": request.repo}
    }
    
    if request.service_name:
        service_input["name"] = request.service_name
    
    if request.root_directory:
        service_input["rootDirectory"] = request.root_directory
    
    variables = {"input": service_input}
    
    try:
        result = client.execute_query(mutation, variables)
        service_data = result["serviceCreate"]
        
        logger.info(f"Service created successfully: {service_data['id']}")
        
        return ServiceResponse(
            id=service_data["id"],
            name=service_data.get("name", "unnamed"),
            created_at=service_data["createdAt"],
            status="created"
        )
        
    except Exception as e:
        logger.error(f"Failed to create service: {e}")
        raise HTTPException(status_code=500, detail=f"Service creation failed: {str(e)}")

@app.post("/variables/set", response_model=VariableResponse)
async def set_variables(
    request: VariableSetRequest,
    client: RailwayClient = Depends(get_railway_client)
):
    """Set environment variables for a Railway service"""
    
    logger.info(f"Setting {len(request.variables)} variables for service: {request.service_id}")
    
    success_vars = []
    error_vars = []
    
    # Get environment ID if not provided
    environment_id = request.environment_id
    if not environment_id:
        query = """
        query project($id: String!) {
          project(id: $id) {
            environments {
              edges {
                node {
                  id
                  name
                }
              }
            }
          }
        }
        """
        
        variables = {"id": request.project_id}
        
        try:
            result = client.execute_query(query, variables)
            environments = result["project"]["environments"]["edges"]
            
            # Look for production environment first
            for env in environments:
                if env["node"]["name"].lower() == "production":
                    environment_id = env["node"]["id"]
                    break
            
            if not environment_id and environments:
                # Use first environment if production not found
                environment_id = environments[0]["node"]["id"]
                
        except Exception as e:
            logger.error(f"Failed to get environment ID: {e}")
            return VariableResponse(
                success=False,
                variables_set=[],
                errors=[f"Failed to get environment ID: {str(e)}"]
            )
    
    # Set each variable
    for key, value in request.variables.items():
        mutation = """
        mutation variableUpsert($input: VariableUpsertInput!) {
          variableUpsert(input: $input)
        }
        """
        
        variables = {
            "input": {
                "projectId": request.project_id,
                "environmentId": environment_id,
                "serviceId": request.service_id,
                "name": key,
                "value": value
            }
        }
        
        try:
            result = client.execute_query(mutation, variables)
            if result.get("variableUpsert") is True:
                success_vars.append(key)
                logger.info(f"Successfully set variable: {key}")
            else:
                error_msg = f"Failed to set {key}: Unexpected response"
                error_vars.append(error_msg)
                logger.error(error_msg)
            
        except Exception as e:
            error_msg = f"Failed to set {key}: {str(e)}"
            error_vars.append(error_msg)
            logger.error(error_msg)
    
    return VariableResponse(
        success=len(error_vars) == 0,
        variables_set=success_vars,
        errors=error_vars
    )

@app.post("/deployments/trigger")
async def trigger_deployment(
    request: DeploymentTriggerRequest,
    client: RailwayClient = Depends(get_railway_client)
):
    """Trigger a new deployment for a service by restarting the latest deployment"""
    
    logger.info(f"Triggering deployment for service: {request.service_id}")
    
    # First, get the latest deployment for the service
    get_deployment_query = """
    query deployments($first: Int, $input: DeploymentListInput!) {
      deployments(first: $first, input: $input) {
        edges {
          node {
            id
            status
            createdAt
            url
            canRedeploy
          }
        }
      }
    }
    """
    
    get_variables = {
        "first": 1,
        "input": {
            "projectId": request.project_id,
            "serviceId": request.service_id,
            "environmentId": request.environment_id
        }
    }
    
    try:
        # Get the latest deployment
        result = client.execute_query(get_deployment_query, get_variables)
        deployments = result["deployments"]["edges"]
        
        if not deployments:
            raise HTTPException(status_code=404, detail="No deployments found for this service")
        
        latest_deployment_id = deployments[0]["node"]["id"]
        
        # Now restart the deployment
        restart_mutation = """
        mutation deploymentRestart($deploymentId: String!) {
          deploymentRestart(id: $deploymentId)
        }
        """
        
        restart_variables = {"deploymentId": latest_deployment_id}
        restart_result = client.execute_query(restart_mutation, restart_variables)
        
        logger.info(f"Deployment restarted: {latest_deployment_id}")
        
        return {
            "success": True,
            "deployment_id": latest_deployment_id,
            "restarted": restart_result["deploymentRestart"]
        }
        
    except Exception as e:
        logger.error(f"Failed to trigger deployment: {e}")
        raise HTTPException(status_code=500, detail=f"Deployment trigger failed: {str(e)}")

@app.get("/services/{service_id}/status")
async def get_service_status(
    service_id: str,
    client: RailwayClient = Depends(get_railway_client)
):
    """Get the current status of a Railway service"""
    
    query = """
    query service($id: String!) {
      service(id: $id) {
        id
        name
        createdAt
      }
    }
    """
    
    variables = {"id": service_id}
    
    try:
        result = client.execute_query(query, variables)
        service = result["service"]
        
        return {
            "service_id": service["id"],
            "name": service["name"],
            "created_at": service["createdAt"],
            "status": "active"
        }
        
    except Exception as e:
        logger.error(f"Failed to get service status: {e}")
        raise HTTPException(status_code=500, detail=f"Status query failed: {str(e)}")

@app.get("/utils/generate-secret")
async def generate_secret_key():
    """Generate a cryptographically secure secret key"""
    secret_key = secrets.token_hex(32)  # 64 character hex string
    
    return {
        "secret_key": secret_key,
        "length": len(secret_key),
        "generated_at": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
