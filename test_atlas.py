import pymongo
from config import Config

print('🧪 Testing MongoDB Atlas (Old Format)...')
print('=' * 50)

config = Config()

try:
    # OLD FORMAT: mongodb:// instead of mongodb+srv://
    connection_string = f'mongodb://{config.MONGODB_USERNAME}:{config.MONGODB_PASSWORD}@{config.MONGODB_HOST}/{config.MONGODB_DATABASE}?retryWrites=true&w=majority'
    
    print(f'📍 Connecting to: {config.MONGODB_HOST}')
    print(f'👤 Username: {config.MONGODB_USERNAME}')
    print(f'🗄️  Database: {config.MONGODB_DATABASE}')
    print(f'🔗 Connection: {connection_string[:50]}...')
    print()
    
    # Connect with longer timeout for DNS issues
    client = pymongo.MongoClient(
        connection_string,
        serverSelectionTimeoutMS=15000  # 15 second timeout
    )
    
    # Test connection
    print('🔗 Testing connection...')
    db = client[config.MONGODB_DATABASE]
    
    # Ping the server
    db.command('ping')
    print('✅ Connected successfully!')
    
    # List collections
    collections = db.list_collection_names()
    print(f'📊 Collections found: {len(collections)}')
    
    if collections:
        print(f'📂 Collections: {collections[:5]}')
    
    # Test write operation
    print()
    print('✍️  Testing write operation...')
    test_collection = db['connection_test']
    result = test_collection.insert_one({
        'message': 'APEX HUNTER V14 Old Format Test',
        'timestamp': '2026-01-10T22:50:00Z',
        'format': 'old_mongodb',
        'dns_bypass': True
    })
    
    print(f'✅ Document inserted with ID: {result.inserted_id}')
    
    # Clean up
    test_collection.delete_one({'_id': result.inserted_id})
    print('🧹 Test document cleaned up')
    
    client.close()
    print()
    print('🎉 MongoDB Atlas (Old Format) is WORKING!')
    print('DNS issues bypassed - your bot will now log to Atlas!')

except pymongo.errors.ServerSelectionTimeoutError:
    print('❌ CONNECTION TIMEOUT')
    print('• Try again with different network')
    print('• Check if Atlas cluster is paused')
    print('• Verify IP whitelist in Atlas')
    
except pymongo.errors.ConfigurationError as e:
    print(f'❌ CONFIGURATION ERROR: {e}')
    print('• Check username/password')
    print('• Verify database name')
    
except Exception as e:
    print(f'❌ CONNECTION FAILED: {e}')
    print('Try the mongodb+srv:// format if old format still fails')

print('=' * 50)
