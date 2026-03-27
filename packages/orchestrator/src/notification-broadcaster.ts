/**
 * NotificationBroadcaster — Multi-channel event fan-out.
 *
 * Replaces single-channel sendNotification (stdout only) with a broadcaster
 * that delivers events to all registered channels simultaneously:
 * - stdio (primary, always present in sidecar mode)
 * - MCP HTTP sessions (SSE via StreamableHTTPServerTransport)
 * - Native SSE connections (optional GET /events endpoint)
 */

export interface NotificationChannel {
  readonly name: string;
  send(method: string, params: Record<string, unknown>): void;
  close(): void;
}

export class NotificationBroadcaster {
  private channels = new Map<string, NotificationChannel>();

  addChannel(channel: NotificationChannel): void {
    this.channels.set(channel.name, channel);
  }

  removeChannel(name: string): void {
    const ch = this.channels.get(name);
    if (ch) {
      ch.close();
      this.channels.delete(name);
    }
  }

  hasChannel(name: string): boolean {
    return this.channels.has(name);
  }

  get channelCount(): number {
    return this.channels.size;
  }

  broadcast(method: string, params: Record<string, unknown>): void {
    for (const ch of this.channels.values()) {
      try {
        ch.send(method, params);
      } catch {
        // Non-fatal: individual channel failure must not block others
      }
    }
  }

  closeAll(): void {
    for (const ch of this.channels.values()) {
      try {
        ch.close();
      } catch {
        // best-effort cleanup
      }
    }
    this.channels.clear();
  }

  /**
   * Wrap an RpcTransport so its sendNotification goes through the broadcaster.
   * The transport's original sendNotification becomes the "stdio" channel.
   */
  static wrapTransport(
    transport: { sendNotification: (method: string, params: Record<string, unknown>) => void },
  ): NotificationBroadcaster {
    const broadcaster = new NotificationBroadcaster();

    // Capture original sendNotification as stdio channel
    const originalSend = transport.sendNotification.bind(transport);
    broadcaster.addChannel({
      name: "stdio",
      send: originalSend,
      close: () => {},
    });

    // Replace transport.sendNotification with broadcaster
    transport.sendNotification = (method: string, params: Record<string, unknown>) => {
      broadcaster.broadcast(method, params);
    };

    return broadcaster;
  }
}
