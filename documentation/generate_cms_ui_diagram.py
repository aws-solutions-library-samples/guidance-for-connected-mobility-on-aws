#!/usr/bin/env python3
"""
Generate AWS Architecture Diagram for CMS UI Frontend
"""

from diagrams import Diagram, Cluster, Edge
from diagrams.aws.general import User, Users
from diagrams.aws.network import CloudFront, APIGateway, Route53
from diagrams.aws.storage import S3
from diagrams.aws.security import Cognito, IAM
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.management import Cloudwatch

def generate_cms_ui_architecture():
    """Generate the CMS UI Frontend Architecture diagram"""
    
    with Diagram(
        "Connected Vehicle Platform - Frontend Architecture",
        show=False,
        direction="TB",
        filename="cms_ui_frontend_architecture",
        graph_attr={
            "fontsize": "16",
            "bgcolor": "white",
            "pad": "1.0",
            "rankdir": "TB"
        }
    ):
        
        # Users
        with Cluster("Users"):
            fleet_manager = User("Fleet Manager")
            admin = User("Administrator")
            
        # Content Delivery Network
        with Cluster("Content Delivery"):
            dns = Route53("DNS")
            cdn = CloudFront("CloudFront CDN")
            
        # Frontend Application
        with Cluster("Frontend Application"):
            react_app = S3("React Application\n(S3 Static Hosting)")
            
        # API Layer
        with Cluster("API Gateway"):
            api_gateway = APIGateway("REST API Gateway")
            
        # Authentication & Authorization
        with Cluster("Authentication"):
            user_pool = Cognito("Cognito User Pool")
            iam_roles = IAM("IAM Roles")
            
        # Backend Services
        with Cluster("Backend Services"):
            fleet_api = Lambda("Fleet Management API")
            vehicle_api = Lambda("Vehicle Data API")
            auth_api = Lambda("Authentication API")
            
        # Data Storage
        with Cluster("Data Layer"):
            fleet_db = Dynamodb("Fleet Database")
            vehicle_db = Dynamodb("Vehicle Database")
            telemetry_storage = S3("Telemetry Data\n(S3)")
            
        # Monitoring & Logging
        with Cluster("Monitoring"):
            logs = Cloudwatch("CloudWatch\nLogs & Metrics")
            
        # User Flow
        fleet_manager >> dns
        admin >> dns
        dns >> cdn
        cdn >> react_app
        
        # API Flow
        react_app >> api_gateway
        api_gateway >> user_pool
        user_pool >> iam_roles
        
        # Backend API Flow
        api_gateway >> fleet_api
        api_gateway >> vehicle_api
        api_gateway >> auth_api
        
        # Data Flow
        fleet_api >> fleet_db
        vehicle_api >> vehicle_db
        vehicle_api >> telemetry_storage
        auth_api >> user_pool
        
        # Monitoring Flow
        fleet_api >> logs
        vehicle_api >> logs
        auth_api >> logs
        api_gateway >> logs

if __name__ == "__main__":
    print("Generating CMS UI Frontend Architecture Diagram...")
    generate_cms_ui_architecture()
    print("✅ Diagram generated: cms_ui_frontend_architecture.png")
    print("📁 Location: ./cms_ui_frontend_architecture.png")
