import logging
from typing import Dict, Any
from workers.core_api.django_api_service import DjangoApiService # <-- O Cliente HTTP

logger = logging.getLogger("metrics-client-service")

def registrar_evento(
    cliente_id: str,
    event_id: str,
    tipo_metrica: str,
    status: str = 'success',
    detalhes: str = '',
) -> Dict[str, Any]:
    """
    [FUNÇÃO UNIFICADA NO WORKER]
    Dispara o log de métrica para o Django BaaS via chamada HTTP.
    
    Esta função substitui a antiga lógica que usava o ORM.
    """
    
    payload = {
        'cliente_id': cliente_id,
        'event_id': event_id,
        'tipo_metrica': tipo_metrica,
        'status': status,
        'detalhes': detalhes,
    }

    # 1. Envia a requisição
    response = DjangoApiService.log_metric(payload)
    
    # 2. Retorna o status (principalmente para logs internos)
    if response.get('status') == 'SUCCESS':
        logger.debug(f"Métrica registrada com sucesso: {tipo_metrica} | {status}")
    else:
        logger.warning(f"Falha ao registrar métrica (HTTP status: {response.get('status')}): {response.get('message')}")
        
    return response

# Funções de listagem/resumo de métricas (se existirem) DEVEM ser alteradas de forma similar,
# com chamadas GET para um novo endpoint no Django BaaS.