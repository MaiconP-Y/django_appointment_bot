# Arquivo: chatbot_api/views.py (NO PROJETO DJANGO BAAS)

import logging
from django.db import transaction
from django.utils import timezone
from datetime import datetime, timedelta
from rest_framework.decorators import api_view # Requer Django Rest Framework (DRF)
from rest_framework.response import Response # Requer DRF
from rest_framework import status
from chatbot_api.models import UserRegister # Seus modelos do Django
from django.db import transaction, IntegrityError
from chatbot_api.models import LogMetrica


logger = logging.getLogger(__name__)

@api_view(['POST'])
@transaction.atomic
def log_metric(request):
    """
    Endpoint HTTP para registrar logs de métrica de forma atômica no PostgreSQL.
    Usado pelo Worker de IA.
    """
    data = request.data
    required_fields = ['cliente_id', 'event_id', 'tipo_metrica']
    if not all(field in data for field in required_fields):
        logger.error(f"❌ Tentativa de log de métrica inválida: {data}")
        return Response(
            {"status": "FAILURE", "message": "Campos obrigatórios ausentes."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        LogMetrica.registrar_evento(
            cliente_id=data.get('cliente_id'),
            event_id=data.get('event_id'),
            tipo_metrica=data.get('tipo_metrica'),
            status=data.get('status', 'success'), 
            detalhes=data.get('detalhes', ''),
        )
        return Response({"status": "SUCCESS", "message": "Métrica registrada."}, status=status.HTTP_201_CREATED)
        
    except IntegrityError as e:
        logger.warning(f"⚠️ Erro de Integridade ao registrar métrica (Rollback): {e}")
        return Response({"status": "FAILURE", "message": "Erro de integridade do DB."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"❌ Erro CRÍTICO ao registrar métrica: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def get_user_data(request, chat_id):
    """
    Endpoint de API para retornar dados de registro de um usuário.
    ✅ AGORA: Implementa o filtro de datas futuras e a formatação (replicando a lógica antiga).
    """
    try:
        user = UserRegister.objects.get(chat_id=chat_id)
        consultas = []
        agora = timezone.now()

        if user.appointment1_gcal_id and user.appointment1_datetime and user.appointment1_datetime >= agora:
            local_dt1 = timezone.localtime(user.appointment1_datetime)
            
            consultas.append({
                "appointment_number": 1,
                "data": local_dt1.strftime("%d/%m/%Y"),
                "hora": local_dt1.strftime("%H:%M"),
                "slot": 1,
                "gcal_id": user.appointment1_gcal_id,
                "datetime_iso": user.appointment1_datetime.isoformat()
            })

        if user.appointment2_gcal_id and user.appointment2_datetime and user.appointment2_datetime >= agora:
            local_dt2 = timezone.localtime(user.appointment2_datetime)

            consultas.append({
                "appointment_number": 2,
                "data": local_dt2.strftime("%d/%m/%Y"),
                "hora": local_dt2.strftime("%H:%M"),
                "slot": 2,
                "gcal_id": user.appointment2_gcal_id,
                "datetime_iso": user.appointment2_datetime.isoformat()
            })

        consultas.sort(key=lambda x: datetime.strptime(f"{x['data']} {x['hora']}", "%d/%m/%Y %H:%M"))
        
        response_data = {
            "status": "SUCCESS",
            "chat_id": user.chat_id,
            "username": user.username,
            "appointments": consultas
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except UserRegister.DoesNotExist:
        return Response({"status": "NOT_FOUND", "message": "Usuário não registrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
                        
    except Exception as e:
        logger.error(f"❌ Erro ao buscar dados de usuário no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@transaction.atomic
def salvar_agendamento_transacional(request):
    """
    Endpoint de API que recebe a requisição do Worker de IA e executa a 
    sua lógica de slots e transação atômica.
    """
    
    chat_id = request.data.get('chat_id')
    google_event_id = request.data.get('google_event_id')
    start_time_iso = request.data.get('start_time_iso')
    
    if not all([chat_id, google_event_id, start_time_iso]):
        return Response({"status": "ERROR", "message": "Parâmetros incompletos."}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        user = UserRegister.objects.select_for_update().get(chat_id=chat_id) 
        new_datetime = datetime.fromisoformat(start_time_iso)
        agora = timezone.now()
        
        is_slot1_free = not user.appointment1_gcal_id or (user.appointment1_datetime and user.appointment1_datetime < agora)
        
        if is_slot1_free:
            user.appointment1_datetime = new_datetime
            user.appointment1_gcal_id = google_event_id
            user.save(update_fields=['appointment1_datetime', 'appointment1_gcal_id'])
            logger.info(f"✅ Agendamento salvo no slot 1 (BaaS) - Cliente: {chat_id}")
            response_data = {"status": "SUCCESS", "slot": 1, "data": new_datetime.strftime('%d/%m/%Y às %H:%M')}
            return Response(response_data, status=status.HTTP_200_OK)

        is_slot2_free = not user.appointment2_gcal_id or (user.appointment2_datetime and user.appointment2_datetime < agora)
        
        if is_slot2_free:
            user.appointment2_datetime = new_datetime
            user.appointment2_gcal_id = google_event_id
            user.save(update_fields=['appointment2_datetime', 'appointment2_gcal_id']) 
            logger.info(f"✅ Agendamento salvo no slot 2 (BaaS) - Cliente: {chat_id}")
            response_data = {"status": "SUCCESS", "slot": 2, "data": new_datetime.strftime('%d/%m/%Y às %H:%M')}
            return Response(response_data, status=status.HTTP_200_OK)
        else:
            return Response({"status": "FAILURE", "message": "Limite de agendamentos atingido. Você pode ter no máximo 2 consultas ativas."}, 
                            status=status.HTTP_409_CONFLICT) # 409 Conflict é adequado
                            
    except UserRegister.DoesNotExist:
        return Response({"status": "FAILURE", "message": "Usuário não registrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
        
    except Exception as e:
        logger.error(f"❌ Erro grave ao salvar agendamento no BaaS: {e}")
        # Retorna erro de servidor para o Worker de IA
        return Response({"status": "ERROR", "message": "Ocorreu um erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST']) 
def register_user(request):
    """
    Endpoint para criar um novo usuário.
    O worker faz um POST aqui para registrar o nome fornecido.
    """
    try:
        with transaction.atomic():
            data = request.data
            chat_id = data.get('chat_id')
            username = data.get('name') 
            
            if not chat_id or not username:
                return Response({"status": "FAILURE", "message": "Campos 'chat_id' e 'name' são obrigatórios."}, 
                                status=status.HTTP_400_BAD_REQUEST)

            user = UserRegister.objects.create(chat_id=chat_id, username=username)

            return Response({
                "status": "SUCCESS", 
                "message": "Usuário registrado com sucesso.", 
                "username": user.username 
            }, status=status.HTTP_201_CREATED)
            
    except IntegrityError:
        # Se o chat_id for UNIQUE e já existir, retorna 409 Conflict
        return Response({"status": "FAILURE", "message": "Usuário já existe."}, 
                        status=status.HTTP_409_CONFLICT) 
        
    except Exception as e:
        logger.error(f"❌ Erro ao registrar usuário: {e}", exc_info=True)
        return Response({"status": "ERROR", "message": "Erro interno do servidor."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
def cancel_appointment_transacional(request):
    """
    Endpoint de API para limpar o slot de agendamento no DB (limpa o slot).
    Garante a atomicidade e o lock de linha.
    """
    chat_id = request.data.get('chat_id')
    numero_consulta = request.data.get('numero_consulta')

    if not all([chat_id, numero_consulta]):
        return Response({"status": "ERROR", "message": "Parâmetros incompletos."}, 
                        status=status.HTTP_400_BAD_REQUEST)
    
    try:
        numero_consulta = int(numero_consulta)
    except ValueError:
         return Response({"status": "ERROR", "message": "numero_consulta deve ser um inteiro."}, 
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            user = UserRegister.objects.select_for_update().get(chat_id=chat_id)
            
            db_slot_cleared = False
            
            if numero_consulta == 1 and user.appointment1_gcal_id:
                user.appointment1_datetime = None
                user.appointment1_gcal_id = None
                user.save(update_fields=['appointment1_datetime', 'appointment1_gcal_id'])
                db_slot_cleared = True
                
            elif numero_consulta == 2 and user.appointment2_gcal_id:
                user.appointment2_datetime = None
                user.appointment2_gcal_id = None
                user.save(update_fields=['appointment2_datetime', 'appointment2_gcal_id'])
                db_slot_cleared = True
                
            if not db_slot_cleared:
                return Response({"status": "FAILURE", "message": f"Não encontrei agendamento ativo no slot {numero_consulta} para limpar."}, 
                                status=status.HTTP_404_NOT_FOUND)

            logger.info(f"✅ Slot {numero_consulta} LIMPO no DB (BaaS) - Cliente: {chat_id}")
            
            return Response({"status": "SUCCESS", "message": "Slot limpo no banco de dados."}, 
                            status=status.HTTP_200_OK)

    except UserRegister.DoesNotExist:
        return Response({"status": "FAILURE", "message": "Usuário não encontrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"❌ Erro grave ao limpar slot {numero_consulta} no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Ocorreu um erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def list_active_appointments(request, chat_id):
    """
    Endpoint de API para retornar uma lista formatada de agendamentos ATIVOS.
    A lógica de 'ativo' e a formatação são executadas AQUI no BaaS.
    """
    try:
        user = UserRegister.objects.get(chat_id=chat_id)
        lista_consultas = []
        agora = timezone.now() 

        if user.appointment1_datetime and user.appointment1_datetime > agora:
            lista_consultas.append({
                "appointment_number": 1, 
                "data": user.appointment1_datetime.strftime('%d/%m/%Y'),
                "hora": user.appointment1_datetime.strftime('%H:%M'),
                "gcal_id": user.appointment1_gcal_id,
                "datetime_iso": user.appointment1_datetime.isoformat()
            })

        if user.appointment2_datetime and user.appointment2_datetime > agora:
            lista_consultas.append({
                "appointment_number": 2, 
                "data": user.appointment2_datetime.strftime('%d/%m/%Y'),
                "hora": user.appointment2_datetime.strftime('%H:%M'),
                "gcal_id": user.appointment2_gcal_id,
                "datetime_iso": user.appointment2_datetime.isoformat()
            })

        return Response({"status": "SUCCESS", "appointments": lista_consultas}, status=status.HTTP_200_OK)
        
    except UserRegister.DoesNotExist:
        return Response({"status": "NOT_FOUND", "appointments": []}, status=status.HTTP_404_NOT_FOUND)
                        
    except Exception as e:
        logger.error(f"❌ Erro ao listar agendamentos ativos no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
@api_view(['POST'])
@transaction.atomic
def cleanup_expired_appointments_view(request):
    """
    Limpa agendamentos que já ocorreram há mais de 2 horas.
    Ex: Se agora é 14:00, limpa tudo agendado para antes das 12:00.
    """
    agora_utc = timezone.now()
    data_limite = agora_utc - timedelta(hours=2)

    logger.info(f"🧹 Iniciando limpeza. Agora(UTC): {agora_utc} | Cortar agendamentos anteriores a: {data_limite}")

    result_slot1 = UserRegister.objects.filter(
        appointment1_datetime__lt=data_limite,
        appointment1_datetime__isnull=False 
    ).update(
        appointment1_datetime=None,
        appointment1_gcal_id=None
    )

    result_slot2 = UserRegister.objects.filter(
        appointment2_datetime__lt=data_limite,
        appointment2_datetime__isnull=False
    ).update(
        appointment2_datetime=None,
        appointment2_gcal_id=None
    )

    total_limpos = result_slot1 + result_slot2
    
    if total_limpos > 0:
        logger.info(f"✅ Limpeza concluída. {total_limpos} slots antigos foram liberados.")
    else:
        logger.info(f"ℹ️ Limpeza executada, mas nenhum agendamento expirado (anterior a {data_limite}) foi encontrado.")
    
    return Response({"status": "SUCCESS", "slots_limpos": total_limpos}, status=status.HTTP_200_OK)    
