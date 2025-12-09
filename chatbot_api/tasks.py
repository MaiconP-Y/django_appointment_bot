from celery import shared_task
# Importe a nova função refatorada
from workers.lembretes.lembrets import process_reminders
import logging

logger = logging.getLogger(__name__)

@shared_task(name="send_scheduled_reminders") # O nome 'send_scheduled_reminders' é usado no settings.py
def send_scheduled_reminders_task():
    """
    [Celery Task] Aciona a lógica de negócio do worker de lembretes.
    """
    process_reminders()
    logger.info("Task de lembretes finalizada pelo Celery.")