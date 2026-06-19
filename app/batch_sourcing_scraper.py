import asyncio
import os
import sys

from pymongo import MongoClient

# Adjust path to enable importing FastAPI app parameters safely
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.enterprise.sourcing.cache import sourcing_cache
from app.services.enterprise.sourcing_service import sourcing_service

# Initialize MongoDB Client
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "croar_sourcing")
mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client[MONGO_DB_NAME]
profiles_collection = mongo_db["candidate_profiles"]


async def batch_scrape():
    await asyncio.sleep(0)  # async kept: invoked via asyncio.run() as a coroutine
    query = "Senior Frontend Developer"
    location = ""
    page_size = 15
    _max_pages = 5

    print("--- BATCH SCRAPING TALENT INTELLIGENCE ---")
    print(f"Target Role: {query}")
    print(f"Platform Sources: {len(sourcing_service.providers)}\n")

    for platform_name, provider in sourcing_service.providers.items():
        print(f"🔍 Scouring {platform_name.upper()}...")
        page = 1
        while True:
            print(f"   📄 Accessing Page {page}...")
            try:
                profiles = provider.search(query, location, page, page_size)
                if profiles and len(profiles) > 0:
                    print(f"      ✅ Discovered {len(profiles)} qualified talent options.")
                    sourcing_cache.set_search_cache(query, location, platform_name, page, profiles)

                    # Store in MongoDB
                    for prof in profiles:
                        # Create a deterministic unique identifier to prevent duplication
                        platform_tag = prof.get("platform", platform_name)
                        unique_id = (
                            prof.get("profile_url")
                            or prof.get("email")
                            or f"{prof.get('full_name')}-{platform_tag}"
                        )

                        profiles_collection.update_one(
                            {"profile_url": unique_id}, {"$set": prof}, upsert=True
                        )
                    print(f"      💾 Persisted {len(profiles)} records to MongoDB collection.")
                    page += 1
                else:
                    print("      ⏹️ No more profiles returned. Moving on.")
                    break
            except Exception as e:
                print(f"      ❌ Failure/Break: {e}")
                break

    print("\n--- BATCH SCRAPING COMPLETE ---")


if __name__ == "__main__":
    asyncio.run(batch_scrape())
