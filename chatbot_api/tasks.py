from celery import shared_task
# Importe a nova função refatorada
from workers.lembretes.lembrets import process_reminders
from workers.cleanup.cleanup_service import run_daily_cleanup
import logging

logger = logging.getLogger(__name__)

@shared_task(name="send_scheduled_reminders")
def send_scheduled_reminders_task():
    """
    [Celery Task] Aciona a lógica de negócio do worker de lembretes.
    """
    process_reminders()
    logger.info("Task de lembretes finalizada pelo Celery.")

@shared_task(name="cleanup_expired_appointments_task")
def cleanup_expired_appointments_task():
    """
    [Celery Task] Aciona a lógica de negócio de limpeza de DB.
    """
    run_daily_cleanup()
    logger.info("Task de limpeza finalizada pelo Celery.")