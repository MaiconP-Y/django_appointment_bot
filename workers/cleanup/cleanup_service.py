import logging
from workers.core_api.django_api_service import DjangoApiService

logger = logging.getLogger(__name__)

def run_daily_cleanup():
    """
    Lógica de negócio para a tarefa de limpeza.
    Chama o endpoint de limpeza no BaaS via HTTP.
    """
    logger.info("Iniciando rotina de limpeza de agendamentos expirados.")
    
    response = DjangoApiService.cleanup_expired_appointments()
    
    if response.get('status') == 'SUCCESS':
        slots_limpos = response.get('slots_limpos', 0)
        logger.info(f"✅ Limpeza do DB executada com sucesso. Total de slots limpos: {slots_limpos}")
    else:
        logger.error(f"❌ Falha na rotina de limpeza do DB: {response.get('message', 'Erro desconhecido')}")