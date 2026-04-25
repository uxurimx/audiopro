Aquí va el resumen ejecutivo del proyecto:                                                                                                    
                              
  ---                                                                                                                                           
  audioPro (paquete: audifonospro)                                                                                                              
                                                                                                                                                
  Qué es: Sistema de audio personal con IA, corriendo en tu laptop con Fedora + GTK4. Permite traducir voz en tiempo real, ver películas en     
  familia donde cada persona escucha el idioma que quiere, y gestionar dispositivos de audio (BT, jack, HDMI).                                  
                  
  Nombre comercial: audioPro — el paquete interno sigue como audifonospro por compatibilidad.                                                   
                  
  ---                                                                                                                                           
  Modos principales

  ┌───────────┬───────────────────────────────────────────────────────────────────────────────┐
  │   Modo    │                                  Descripción                                  │
  ├───────────┼───────────────────────────────────────────────────────────────────────────────┤                                                 
  │ Traductor │ Mic → VAD → Whisper → LLM → TTS → audífonos en tiempo real                    │
  ├───────────┼───────────────────────────────────────────────────────────────────────────────┤                                                 
  │ Cinema    │ MKV multi-track → GStreamer → cada persona escucha su pista en su dispositivo │                                                 
  ├───────────┼───────────────────────────────────────────────────────────────────────────────┤                                                 
  │ Monitor   │ Batería, codec, RSSI, PipeWire stats en vivo                                  │                                                 
  └───────────┴───────────────────────────────────────────────────────────────────────────────┘                                                 
                  
  ---                                                                                                                                           
  Estado actual (Fase 4 en progreso)
                                                                                                                                                
  Completado:
  - Fases 0–3: fundación, monitor de dispositivos, audio I/O, ANC software/hardware (BLE GATT)                                                  
  - GTK4 UI completa (6 tabs funcionales)                                                                                                       
  - GNOME Shell Extension (Quick Settings tile)
  - DB SQLite (sesiones, frases, historial)                                                                                                     
  - Pipeline de traducción: funciona (VAD→STT→LLM→TTS end-to-end)                                                                               
                                                                                                                                                
  Pendiente:                                                                                                                                    
  - Fase 4: pulir el pipeline (la translator_page.py está modificada = hay trabajo en curso)                                                    
  - Fase 5: mapeo de botones físicos (evdev) + perfiles por persona                                                                             
                  
  ---                                                                                                                                           
  Pros            
                                                                                                                                                
  - Local-first real: Whisper.cpp + Ollama + edge-tts = $0/sesión en modo offline
  - Stack sólido: GTK4 + libadwaita, se ve como app GNOME nativa                                                                                
  - Arquitectura limpia: pipeline con 5 threads + 4 colas, sin bloquear el UI                                                                   
  - Flexible: 3 modos de calidad (local/equilibrado/cloud) configurables en runtime                                                             
                                                                                                                                                
  Contras                                                                                                                                       
                                                                                                                                                
  - Sin GPU: Ollama en CPU puro = ~10s/frase en modo local (el modo "equilibrado" usa GPT-4o-mini cloud)                                        
  - Fase Cinema incompleta: el GStreamer multi-track/multi-dispositivo no está acabado
  - Fase 5 intacta: evdev + perfiles por persona = sin accesos directos físicos aún                                                             
  - Un solo usuario real: JBL Vive Buds tuyas, no probado con más dispositivos                                                                  
                                                                                                                                                
  ---                                                                                                                                           
  Prioridad recomendada                                                                                                                         
                       
  Es un proyecto de uso personal con potencial de producto. La funcionalidad core (traductor) ya funciona. Si el objetivo es usarlo ya: pulir
  Fase 4. Si el objetivo es lanzarlo como producto: Fase Cinema y perfiles por persona son los diferenciadores.   
  
  Proximas  mejoras:
  
  + Cancelacion de sonido por software, hay algunos audifonos que ya tienen  microfono dentro y no lo usan, hacer algo con eso.
  + Interfaz para botones sinapticos.
  
