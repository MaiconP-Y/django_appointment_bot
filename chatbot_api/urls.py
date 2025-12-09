from django.urls import path
from . import views

urlpatterns = [
    # Rotas de Agendamento (Estáticas)
    path('agendamentos/salvar/', views.salvar_agendamento_transacional, name='salvar_agendamento'),
    path('agendamentos/cancelar/', views.cancel_appointment_transacional, name='cancelar_agendamento'),
    
    # 1. Rotas de Usuário ESTÁTICAS (mais específicas) vêm PRIMEIRO
    path('user/register/', views.register_user, name='register_user'), # ✅ Corrigido
    
    # 2. Rotas de Usuário DINÂMICAS (menos específicas) vêm DEPOIS
    path('user/<str:chat_id>/', views.get_user_data, name='get_user_data'), # ✅ Corrigido
    
    # Rota de Métricas
    path('metrics/log/', views.log_metric, name='log_metric'),
]