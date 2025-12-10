from django.urls import path
from . import views

urlpatterns = [
    path('user/register/', views.register_user, name='register_user'), # ✅ Corrigido
    path('cleanup/', views.cleanup_expired_appointments_view, name='cleanup_expired_appointments'),
    path('agendamentos/salvar/', views.salvar_agendamento_transacional, name='salvar_agendamento'),
    path('agendamentos/cancelar/', views.cancel_appointment_transacional, name='cancelar_agendamento'),
    path('user/<str:chat_id>/', views.get_user_data, name='get_user_data'), # ✅ Corrigido
    path('metrics/log/', views.log_metric, name='log_metric'),
]