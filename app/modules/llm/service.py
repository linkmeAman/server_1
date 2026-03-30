"""
LLM Controller - Chat interface for local LLM
Proxies requests to the local LLM server and returns clean responses for the chat UI
"""
import logging
import os
import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime


logger = logging.getLogger(__name__)

# LLM Server Configuration
LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:8000")  # Default to localhost if not set
LLM_DEFAULT_MODEL = os.getenv("LLM_DEFAULT_MODEL", "phi-3")  # Default model to use if not specified
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120.0"))  # 2 minutes timeout for LLM responses


def health():
    """
    Check if the LLM server is available
    
    Returns:
        dict: Health status of the LLM controller and server
    """
    try:
        # Try to connect to the LLM server
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{LLM_API_URL}/v1/models")
            
            if response.status_code == 200:
                models_data = response.json()
                return {
                    "controller": "llm",
                    "status": "healthy",
                    "llm_server": "connected",
                    "llm_url": LLM_API_URL,
                    "available_models": models_data.get("data", []),
                    "default_model": LLM_DEFAULT_MODEL
                }
            else:
                return {
                    "controller": "llm",
                    "status": "degraded",
                    "llm_server": "error",
                    "llm_url": LLM_API_URL,
                    "error": f"LLM server returned status {response.status_code}"
                }
                
    except httpx.ConnectError:
        logger.error(f"Cannot connect to LLM server at {LLM_API_URL}")
        return {
            "controller": "llm",
            "status": "unhealthy",
            "llm_server": "disconnected",
            "llm_url": LLM_API_URL,
            "error": "Cannot connect to LLM server"
        }
    except Exception as e:
        logger.error(f"Error checking LLM health: {e}")
        return {
            "controller": "llm",
            "status": "unhealthy",
            "llm_server": "error",
            "error": str(e)
        }


def models():
    """
    Get list of available models from the LLM server
    
    Returns:
        dict: List of available models
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{LLM_API_URL}/v1/models")
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "models": data.get("data", []),
                    "default_model": LLM_DEFAULT_MODEL
                }
            else:
                return {
                    "models": [],
                    "error": f"Failed to fetch models: {response.status_code}"
                }
                
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        return {
            "models": [],
            "error": str(e)
        }


def chat(
    message: str = None,
    messages: List[Dict[str, str]] = None,
    model: str = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    system_prompt: str = None
):
    """
    Send a chat message to the LLM and get a response
    
    Args:
        message: Single message string (for simple chat)
        messages: Full conversation history (list of {role, content} dicts)
        model: Model to use (default: phi-3)
        temperature: Creativity level 0.0-2.0 (default: 0.7)
        max_tokens: Maximum response length (default: 2048)
        system_prompt: Optional system prompt to set context
    
    Returns:
        dict: LLM response with message content and metadata
    
    Example request body:
        {"message": "Hello, how are you?"}
        
        or with full conversation:
        {"messages": [{"role": "user", "content": "Hello"}], "temperature": 0.5}
    """
    # Validate input
    if not message and not messages:
        logger.warning("Chat called without message or messages")
        return {
            "response": None,
            "error": "Please provide either 'message' or 'messages' parameter"
        }
    
    # Use default model if not specified
    model = model or LLM_DEFAULT_MODEL
    
    # Build messages array
    chat_messages = []
    
    # Add system prompt if provided
    if system_prompt:
        chat_messages.append({
            "role": "system",
            "content": system_prompt
        })
    
    # Add conversation messages
    if messages:
        # Full conversation provided
        chat_messages.extend(messages)
    elif message:
        # Single message provided
        chat_messages.append({
            "role": "user",
            "content": message
        })
    
    # Build request payload
    payload = {
        "model": model,
        "messages": chat_messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    logger.info(f"Sending chat request to LLM: model={model}, messages={len(chat_messages)}")
    
    try:
        with httpx.Client(timeout=LLM_TIMEOUT) as client:
            response = client.post(
                f"{LLM_API_URL}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Extract the assistant's response
                choices = data.get("choices", [])
                if choices:
                    assistant_message = choices[0].get("message", {})
                    content = assistant_message.get("content", "")
                    finish_reason = choices[0].get("finish_reason", "unknown")
                else:
                    content = ""
                    finish_reason = "no_response"
                
                # Extract usage stats
                usage = data.get("usage", {})
                timings = data.get("timings", {})
                
                return {
                    "response": content.strip(),
                    "model": data.get("model", model),
                    "finish_reason": finish_reason,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0)
                    },
                    "timings": {
                        "prompt_ms": timings.get("prompt_ms", 0),
                        "predicted_ms": timings.get("predicted_ms", 0),
                        "tokens_per_second": timings.get("predicted_per_second", 0)
                    },
                    "id": data.get("id", ""),
                    "created": data.get("created", 0)
                }
            else:
                logger.error(f"LLM request failed with status {response.status_code}: {response.text}")
                return {
                    "response": None,
                    "error": f"LLM request failed with status {response.status_code}",
                    "details": response.text[:500] if response.text else None
                }
                
    except httpx.TimeoutException:
        logger.error("LLM request timed out")
        return {
            "response": None,
            "error": "Request timed out. The LLM server took too long to respond."
        }
    except httpx.ConnectError:
        logger.error(f"Cannot connect to LLM server at {LLM_API_URL}")
        return {
            "response": None,
            "error": f"Cannot connect to LLM server at {LLM_API_URL}"
        }
    except Exception as e:
        logger.error(f"Error in chat request: {e}", exc_info=True)
        return {
            "response": None,
            "error": str(e)
        }


def complete(
    prompt: str = None,
    model: str = None,
    temperature: float = 0.7,
    max_tokens: int = 2048
):
    """
    Simple completion endpoint - send a prompt and get a response
    This is a simplified version of chat() for single-turn conversations
    
    Args:
        prompt: The prompt/question to send
        model: Model to use (default: phi-3)
        temperature: Creativity level 0.0-2.0 (default: 0.7)
        max_tokens: Maximum response length (default: 2048)
    
    Returns:
        dict: Simple response with just the text
    
    Example:
        {"prompt": "Write a haiku about coding"}
    """
    if not prompt:
        return {
            "response": None,
            "error": "Please provide a 'prompt' parameter"
        }
    
    # Use the chat function with single message
    result = chat(
        message=prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens
    )
    
    # Return simplified response
    return {
        "prompt": prompt,
        "response": result.get("response"),
        "error": result.get("error"),
        "tokens": result.get("usage", {}).get("total_tokens", 0) if result.get("usage") else 0
    }


def conversation(
    messages: List[Dict[str, str]] = None,
    new_message: str = None,
    model: str = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    system_prompt: str = "You are a helpful AI assistant."
):
    """
    Handle a full conversation with history
    Send existing messages and a new message, get the updated conversation
    
    Args:
        messages: Existing conversation history
        new_message: New message to add
        model: Model to use
        temperature: Creativity level
        max_tokens: Max response tokens
        system_prompt: System prompt for context
    
    Returns:
        dict: Full conversation including the new response
    
    Example:
        {
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"}
            ],
            "new_message": "How are you?",
            "system_prompt": "You are a friendly assistant"
        }
    """
    if not messages:
        messages = []
    
    if not new_message:
        return {
            "conversation": messages,
            "error": "Please provide a 'new_message' parameter"
        }
    
    # Add the new user message
    messages.append({
        "role": "user",
        "content": new_message
    })
    
    # Get LLM response
    result = chat(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        system_prompt=system_prompt
    )
    
    # Add assistant response to conversation
    if result.get("response"):
        messages.append({
            "role": "assistant",
            "content": result["response"]
        })
    
    return {
        "conversation": messages,
        "last_response": result.get("response"),
        "error": result.get("error"),
        "usage": result.get("usage")
    }
