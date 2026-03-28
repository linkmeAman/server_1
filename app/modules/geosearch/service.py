"""
Geosearch Controller - Venue search functionality matching original API exactly
Only includes the search functionality that matches venue_api/main.py
"""
from decimal import Decimal, getcontext
import math
import logging
from typing import Optional, List, Dict, Any, Tuple
from app.core.database import get_db_session, Venue, City
from sqlalchemy import func, and_

# Set high precision for decimal calculations
getcontext().prec = 50

logger = logging.getLogger(__name__)

def get_location_coordinates(location: str) -> Optional[Tuple[float, float]]:
    """Get coordinates from cities table - matches original logic exactly"""
    try:
        db = get_db_session()
        
        # Search for exact match first
        city = db.query(City).filter(
            City.name.ilike(f"{location.strip()}")
        ).first()
        
        if city and city.latitude and city.longitude:
            lat_precise = float(city.latitude)
            lng_precise = float(city.longitude)
            logger.info(f"Found {location} in cities table: ({lat_precise:.15f}, {lng_precise:.15f})")
            return (lat_precise, lng_precise)
        
        # Enhanced matching variations for better city name matching
        search_variations = [
            location.strip(),
            location.strip().replace(" ", ""),
            location.strip().replace(" ", "_"),
            location.strip().replace(" ", "-"),
            location.strip().upper(),
            location.strip().lower(),
            location.strip().title()
        ]
        
        for variation in search_variations:
            city = db.query(City).filter(
                City.name.ilike(f"{variation}")
            ).first()
            
            if city and city.latitude and city.longitude:
                lat_precise = float(city.latitude)
                lng_precise = float(city.longitude)
                logger.info(f"Found {location} in cities table with variation '{variation}': {city.name} ({lat_precise:.15f}, {lng_precise:.15f})")
                return (lat_precise, lng_precise)
        
        # If no exact match, try partial match
        city = db.query(City).filter(
            City.name.ilike(f"%{location.strip()}%")
        ).first()
        
        if city and city.latitude and city.longitude:
            lat_precise = float(city.latitude)
            lng_precise = float(city.longitude)
            logger.info(f"Found partial match for {location} in cities table: {city.name} ({lat_precise:.15f}, {lng_precise:.15f})")
            return (lat_precise, lng_precise)
        
        return None
        
    except Exception as e:
        logger.error(f"Error querying cities table for '{location}': {str(e)}")
        return None
    finally:
        if 'db' in locals():
            db.close()

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points - matches original exactly"""
    # Convert to Decimal for high precision
    lat1_d = Decimal(str(lat1))
    lon1_d = Decimal(str(lon1))
    lat2_d = Decimal(str(lat2))
    lon2_d = Decimal(str(lon2))

    # Convert to radians
    pi = Decimal('3.1415926535897932384626433832795028841971693993751')
    lat1_rad = lat1_d * pi / Decimal('180')
    lon1_rad = lon1_d * pi / Decimal('180')
    lat2_rad = lat2_d * pi / Decimal('180')
    lon2_rad = lon2_d * pi / Decimal('180')

    # Haversine formula
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    # High precision sin and cos using math functions but converting back to Decimal
    a = (Decimal(str(math.sin(float(dlat/2)))) ** 2 + 
         Decimal(str(math.cos(float(lat1_rad)))) * 
         Decimal(str(math.cos(float(lat2_rad)))) * 
         Decimal(str(math.sin(float(dlon/2)))) ** 2)

    c = 2 * Decimal(str(math.asin(float(a.sqrt()))))

    # Earth radius in km with high precision
    earth_radius = Decimal('6371.0088')

    return float(c * earth_radius)

def search(
    location: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius: float = 10.0,
    limit: int = 50
):
    """Search venues - exactly like original API in venue_api/main.py"""
    try:
        # Validate the request - matches original exactly
        if not location and not (lat and lng):
            logger.warning("Missing location or coordinates")
            return []
        
        if lat and lng and not (-90 <= lat <= 90 and -180 <= lng <= 180):
            logger.warning("Invalid coordinate range")
            return []
        
        # Check if coordinates are provided directly
        if lat is not None and lng is not None:
            logger.info(f"Using provided coordinates: ({lat:.15f}, {lng:.15f})")
            center_lat, center_lng = lat, lng
        elif location:
            # Get coordinates for the search location using existing logic
            coordinates = get_location_coordinates(location)
            if not coordinates:
                logger.warning(f"Could not find coordinates for location: {location}")
                return []
            center_lat, center_lng = coordinates
        else:
            logger.error("Neither coordinates nor location provided")
            return []
        
        db = get_db_session()
        
        # Enhanced bounding box calculation with higher precision - matches original
        lat_km_per_degree = Decimal('111.32')  # More accurate value
        lng_km_per_degree = lat_km_per_degree * Decimal(str(math.cos(math.radians(float(center_lat)))))
        
        # Calculate deltas with high precision
        radius_decimal = Decimal(str(radius))
        lat_delta = float(radius_decimal / lat_km_per_degree)
        lng_delta = float(radius_decimal / lng_km_per_degree)
        
        # Add buffer for precision issues
        buffer_factor = 1.15  # 15% buffer for better coverage
        lat_delta *= buffer_factor
        lng_delta *= buffer_factor
        
        # Log search parameters for debugging
        logger.info(f"Search center: ({center_lat:.15f}, {center_lng:.15f})")
        logger.info(f"Search radius: {radius}km")
        logger.info(f"Bounding box: lat({center_lat - lat_delta:.15f}, {center_lat + lat_delta:.15f}), lng({center_lng - lng_delta:.15f}, {center_lng + lng_delta:.15f})")
        
        # Query venues within bounding box - IMPORTANT: matches original exactly
        venues = db.query(Venue).filter(
            and_(
                Venue.lat.between(center_lat - lat_delta, center_lat + lat_delta),
                Venue.lng.between(center_lng - lng_delta, center_lng + lng_delta),
                Venue.lat.isnot(None),
                Venue.lng.isnot(None),
                Venue.lat != '',  # Filter out empty strings that cause float conversion errors
                Venue.lng != '',  # Filter out empty strings that cause float conversion errors  
                Venue.status == 0,  # Original uses status 0, not 1!
                Venue.show_on_website == 1
            )
        ).all()
        
        logger.info(f"Found {len(venues)} venues in bounding box")
        
        # Calculate exact distances and filter with high precision - matches original
        venues_with_distance = []
        
        for venue in venues:
            if venue.lat and venue.lng:
                try:
                    # Preserve full precision from database, handle invalid values
                    venue_lat = float(venue.lat)
                    venue_lng = float(venue.lng)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Skipping venue {venue.id} - invalid coordinates: lat='{venue.lat}', lng='{venue.lng}' - {str(e)}")
                    continue
                
                distance = haversine_distance(
                    center_lat, center_lng, 
                    venue_lat, venue_lng
                )
                
                logger.info(f"Venue {venue.venue} in {venue.city}: distance = {distance:.6f}km (lat: {venue_lat:.15f}, lng: {venue_lng:.15f})")
                
                if distance <= radius:
                    # Convert venue to dict and add distance
                    venue_dict = venue.to_dict()
                    venue_dict['distance'] = round(distance, 6)
                    venue_dict['center_lat'] = center_lat
                    venue_dict['center_lng'] = center_lng
                    venues_with_distance.append(venue_dict)
        
        # Sort by distance and limit results - matches original
        venues_with_distance.sort(key=lambda x: x['distance'])
        logger.info(f"Found {len(venues_with_distance)} venues within {radius}km radius")
        
        return venues_with_distance[:limit]
        
    except Exception as e:
        logger.error(f"Error searching venues: {str(e)}")
        return []
    finally:
        if 'db' in locals():
            db.close()

# Keep only basic health check for testing
def health() -> Dict[str, Any]:
    """Basic health check"""
    logger.info("Geosearch health check called")
    
    try:
        db = get_db_session()
        venue_count = db.query(func.count(Venue.id)).scalar()
        db.close()
        
        return {
            "controller": "geosearch",
            "status": "healthy",
            "database": "connected",
            "total_venues": venue_count
        }
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return {
            "controller": "geosearch",
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e)
        }

