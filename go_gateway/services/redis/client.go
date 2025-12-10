package redis

import (
	"context"
	"encoding/json" // NOVO: Para analisar o JSON
	"errors"        // NOVO: Para retornar erros
	"fmt"
	"os"
	"time"

	"github.com/go-redis/redis/v8"
)

// ChannelName agora é o nome da nossa LISTA/FILA
const ChannelName = "new_user_queue"

// Client (será inicializado uma vez)
var Client *redis.Client

// --- NOVAS CONSTANTES PARA IDEMPOTÊNCIA ---
const idempotencyKeyPrefix = "idempotency:event:" // O prefixo para as chaves no Redis (boa prática)
const idempotencyTTL = time.Hour * 3             // TTL (Tempo de Vida) da chave: 24 horas (otimizado)

// Estrutura Mínima para extrair o ID do Payload WAHA.
// O campo `json:"id"` diz ao Go para procurar o campo "id" no JSON bruto.
type EventPayload struct {
	ID string `json:"id"` 
}

// InitClient configura e testa a conexão com o Redis
func InitClient(ctx context.Context) error {
    // ... (Código existente de inicialização e PING) ...
	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "redis" // Default Docker Compose
	}

	Client = redis.NewClient(&redis.Options{
		Addr: fmt.Sprintf("%s:%s", redisHost, "6379"),
		DB:   0, 
	})

	// Teste de conexão: PING com um timeout seguro de 3 segundos
	pingCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	
	_, err := Client.Ping(pingCtx).Result()
	if err != nil {
		return fmt.Errorf("falha ao conectar e pingar o Redis: %w", err)
	}
	
	return nil
}

// PublishMessage publica o payload bruto na fila (RPUSH)
func PublishMessage(ctx context.Context, rawBody []byte) error {
    publishCtx, cancel := context.WithTimeout(ctx, 100*time.Millisecond)
    defer cancel()
    
    // ➡️ RPUSH (Right Push) para garantir FIFO. Apenas enfileira o payload BRUTO.
    if err := Client.RPush(publishCtx, ChannelName, rawBody).Err(); err != nil {
        return fmt.Errorf("falha ao publicar mensagem no Redis: %w", err)
    }
    
    return nil
}

// =========================================================================
// === NOVA LÓGICA DE IDEMPOTÊNCIA ===
// =========================================================================

// ExtractEventID: Analisa o JSON bruto para obter o ID único.
// É crucial para extrair o ID antes de fazer o SETNX.
func ExtractEventID(rawBody []byte) (string, error) {
	var payload EventPayload
    
    // json.Unmarshal tenta mapear o JSON no array de bytes (rawBody) para a struct 'payload'.
	if err := json.Unmarshal(rawBody, &payload); err != nil {
		return "", fmt.Errorf("falha ao desserializar ID do payload: %w", err)
	}
    
    // Se o ID estiver vazio, é provavelmente uma notificação de status (não duplicata de mensagem).
	if payload.ID == "" {
		return "", errors.New("campo 'id' único não encontrado ou vazio no payload")
	}

	return payload.ID, nil
}

// CheckAndSetIdempotency: Tenta registrar o eventID no Redis.
// SETNX (Set if Not Exists) é a operação atômica (simultânea) que previne a duplicata.
// Retorna (isDuplicate, error).
func CheckAndSetIdempotency(ctx context.Context, eventID string) (bool, error) {
	key := idempotencyKeyPrefix + eventID // Ex: "idempotency:event:AC53DEF098950AC3..."

	// Client.SetNX(context, key, value, expiration)
    // 1. Se a chave NÃO existir, ele a cria e retorna 'true'.
    // 2. Se a chave JÁ EXISTIR, ele NÃO a cria e retorna 'false'.
	result, err := Client.SetNX(ctx, key, "1", idempotencyTTL).Result()

	if err != nil {
		// Falha de comunicação com o Redis (ex: rede caiu).
		return false, fmt.Errorf("erro de comunicação com Redis durante SETNX: %w", err)
	}

	// Queremos saber se É uma duplicata.
	// Se result == false, significa que a chave JÁ EXISTIA (Duplicata).
	return !result, nil
}