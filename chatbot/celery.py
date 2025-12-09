import os
from celery import Celery

# Define o módulo de settings do Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chatbot.settings')

# Cria a instância da aplicação Celery
app = Celery('chatbot')

# Usa a configuração de settings do Django
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-descobre tarefas em todos os apps instalados
app.autodiscover_tasks()