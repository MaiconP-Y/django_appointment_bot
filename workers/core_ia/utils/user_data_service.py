import logging
# ⭐️ Dependências Externas (Isso garante que o módulo é independente)
from core_api.django_api_service import DjangoApiService 
from services.redis_client import get_user_profile_cache, set_user_profile_cache

logger = logging.getLogger(__name__)

def get_user_data_full_cached(chat_id: str) -> dict | None:
    """
    Função central que busca dados COMPLETOS (incluindo agendamentos) no BaaS, 
    utilizando o Redis Cache como primeira linha.
    """
    
    # 1. TENTAR LER DO CACHE REDIS
    cached_user_data = get_user_profile_cache(chat_id)
    
    if cached_user_data:
        logger.info(f"✅ User data COMPLETO para {chat_id} ENCONTRADO no Redis Cache.")
        return cached_user_data

    # 2. SE NÃO ESTIVER NO CACHE, BUSCAR NO DJANGO (Cache Miss)
    logger.info(f"⏳ User data para {chat_id} não encontrado no cache. Buscando via HTTP...")
    
    try:
        db_response = DjangoApiService.get_user_data(chat_id) 
        
        if db_response and db_response.get('status') == 'SUCCESS':
            # 3. SALVAR NO CACHE
            set_user_profile_cache(chat_id, db_response) 
            logger.info(f"💾 User data de {chat_id} salvo no cache com TTL de 3h.")
            return db_response
            
        logger.warning(f"⚠️ Usuário {chat_id} não encontrado no BaaS ou erro HTTP.")
        return None 

    except Exception as e:
        logger.error(f"❌ Erro CRÍTICO HTTP ao buscar dados de user para {chat_id}: {e}", exc_info=True)
        return None


def get_user_name_from_db(chat_id: str) -> str | None:
    """
    Busca o nome do usuário, utilizando a função centralizada de cache.
    """
    full_data = get_user_data_full_cached(chat_id)
    
    if full_data:
        return full_data.get('username')
        
    return None