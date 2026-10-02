"""
Subscriber Initializer Module - main_subscriber.py

Entry point for video streaming subscriber applications.
Handles the reception, processing, and visualization of multiple video streams
in a grid layout with real-time metrics and diagnostics.
"""

from data_stream.subs_stream_manager import StreamManagerSub
from user_interface.sub_test_visualizer import GridVisualizer
from utils import pub_sub_conf, connection_conf

"""
    Main entry point for the subscriber application.

    Orchestrates the complete video subscription and visualization pipeline:
    1. Configuration loading from centralized config modules
    2. Stream manager initialization for data reception and processing
    3. Grid visualizer setup for multi-stream display
    4. Subscriber configuration and connection to middleware
    5. Main visualization loop with real-time metrics
    6. Graceful shutdown and resource cleanup

    Architecture:
        ┌─────────────────────────────────────────────────────────┐
        │                    MAIN SUBSCRIBER                      │
        ├─────────────────────────────────────────────────────────┤
        │ 1. Load Configuration  • topic_prefix, num_streams      │
        │                        • grid_cols, cell dimensions     │
        │                        • padding                        │
        ├─────────────────────────────────────────────────────────┤
        │ 2. Initialize Components                                │
        │    • StreamManagerSub: Data reception & metrics         │
        │    • GridVisualizer: Multi-stream visualization         │
        ├─────────────────────────────────────────────────────────┤
        │ 3. Setup Connections                                    │
        │    • Configure middleware subscribers                   │
        │    • Establish connections to all streams               │
        ├─────────────────────────────────────────────────────────┤
        │ 4. Main Visualization Loop                              │
        │    • Receive frames from multiple streams               │
        │    • Calculate real-time metrics (FPS, latency, loss)   │
        │    • Compose grid layout with overlays                  │
        │    • Display interactive UI with controls               │
        ├─────────────────────────────────────────────────────────┤
        │ 5. Cleanup Phase                                        │
        │    • Stop visualization                                 │
        │    • Close stream connections                           │
        │    • Release all resources                              │
        └─────────────────────────────────────────────────────────┘

    Keyboard Controls (in GridVisualizer):
        - 'q': Quit application
        - 'd': Show final statistics

    Middleware Support:
        - eCAL: Low-latency, embedded systems
        - RabbitMQ: Distributed systems, enterprise

    Returns:
        None
    """
def main():
    # Configuration from utils/config
    topic_prefix = connection_conf.TOPIC_PREFIX
    num_streams = pub_sub_conf.NUM_STREAMS
    grid_cols = pub_sub_conf.GRID_COLS
    cell_width = pub_sub_conf.CELL_WIDTH
    cell_height = pub_sub_conf.CELL_HEIGHT
    padding = pub_sub_conf.PADDING

    # Instances for resource management
    stream_manager = None
    grid_visualizer = None

    try:
        # 1. Create Stream Manager (Data ONLY)
        stream_manager = StreamManagerSub(
            topic_prefix=topic_prefix,
            num_streams=num_streams
        )
        # 2. Create Grid Visualizer (UI ONLY)
        grid_visualizer = GridVisualizer(
            stream_manager=stream_manager,  # Dependency injection
            cell_width=cell_width,
            cell_height=cell_height,
            grid_cols=grid_cols,
            padding=padding
        )
        # 3. Configure subscribers (connections ONLY)
        stream_manager.setup_subscribers()
        print("\n⏳ Waiting for publisher data...")
        # 4. Start Viewing (UI ONLY)
        grid_visualizer.run_visualization()

    except KeyboardInterrupt:
        print("\n🛑 Application stopped by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 5. Orderly cleaning
        print("🔄 Cleaning up resources...")
        try:
            if grid_visualizer:
                grid_visualizer.stop()
        except Exception as e:
            print(f"⚠️ Error stopping visualizer: {e}")

        try:
            if stream_manager:
                stream_manager.stop()
        except Exception as e:
            print(f"⚠️ Error stopping stream manager: {e}")

        print("✅ Application finished correctly")


if __name__ == "__main__":
    main()