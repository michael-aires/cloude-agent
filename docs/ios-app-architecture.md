# Cloude Agent — iOS App Architecture

## Overview

A native iOS application providing an agentic coding assistant and general-purpose AI chat interface, powered by a cloud-hosted Claude Code backend via the Cloude Agent Gateway API.

**Target**: iOS 17+, iPhone + iPad
**Language**: Swift
**UI Framework**: SwiftUI
**Architecture**: MVVM + Coordinator

---

## System Architecture

```
┌─────────────────────────────────────────────────┐
│                 iOS Application                  │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  Chat    │  │ Canvas   │  │  Settings     │  │
│  │  View    │  │ Preview  │  │  View         │  │
│  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
│       │              │                │          │
│  ┌────┴──────────────┴────────────────┴───────┐  │
│  │            ViewModels (MVVM)               │  │
│  └────────────────────┬───────────────────────┘  │
│                       │                          │
│  ┌────────────────────┴───────────────────────┐  │
│  │             Service Layer                   │  │
│  │  ┌─────────┐ ┌──────────┐ ┌─────────────┐  │  │
│  │  │ API     │ │ SSE      │ │ Auth        │  │  │
│  │  │ Client  │ │ Stream   │ │ Manager     │  │  │
│  │  └─────────┘ └──────────┘ └─────────────┘  │  │
│  └────────────────────┬───────────────────────┘  │
│                       │                          │
│  ┌────────────────────┴───────────────────────┐  │
│  │           Local Persistence                 │  │
│  │  ┌──────────┐ ┌───────────┐ ┌───────────┐  │  │
│  │  │ SwiftData│ │ Keychain  │ │ FileManager│  │  │
│  │  └──────────┘ └───────────┘ └───────────┘  │  │
│  └────────────────────────────────────────────┘  │
└───────────────────────┬──────────────────────────┘
                        │ HTTPS / SSE
                        ▼
┌───────────────────────────────────────────────────┐
│            Cloude Agent Backend                    │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │  Gateway API (/api/v1/)                     │  │
│  │  Auth · Conversations · Streaming · Artifacts│  │
│  └──────────────────────┬──────────────────────┘  │
│                         │                         │
│  ┌──────────────────────┴──────────────────────┐  │
│  │  Core API                                   │  │
│  │  AgentManager · Claude SDK · Redis · Skills │  │
│  └─────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┘
```

---

## Project Structure

```
CloudeAgent/
├── App/
│   ├── CloudeAgentApp.swift           # App entry point
│   ├── AppCoordinator.swift           # Navigation coordinator
│   └── AppState.swift                 # Global app state (ObservableObject)
│
├── Features/
│   ├── Auth/
│   │   ├── AuthView.swift             # API key entry / onboarding
│   │   ├── AuthViewModel.swift        # Device registration logic
│   │   └── KeychainService.swift      # Secure token storage
│   │
│   ├── Conversations/
│   │   ├── ConversationListView.swift # Main conversation list
│   │   ├── ConversationListVM.swift   # List view model
│   │   ├── ConversationRow.swift      # Single conversation row
│   │   └── ConversationActions.swift  # Swipe actions (pin, archive, delete)
│   │
│   ├── Chat/
│   │   ├── ChatView.swift             # Chat message thread
│   │   ├── ChatViewModel.swift        # Message sending, streaming
│   │   ├── MessageBubble.swift        # Message display (markdown)
│   │   ├── MessageInput.swift         # Text input + attachments
│   │   ├── ToolIndicator.swift        # Tool usage badges
│   │   ├── ArtifactCard.swift         # Inline artifact preview card
│   │   └── StreamingIndicator.swift   # Typing/thinking indicator
│   │
│   ├── Canvas/
│   │   ├── CanvasView.swift           # Artifact preview panel
│   │   ├── CanvasViewModel.swift      # File loading, type detection
│   │   ├── HTMLPreview.swift          # WKWebView for HTML artifacts
│   │   ├── MarkdownPreview.swift      # Rendered markdown display
│   │   ├── PDFPreview.swift           # PDFKit document viewer
│   │   ├── ImagePreview.swift         # Zoomable image viewer
│   │   └── CodePreview.swift          # Syntax-highlighted code
│   │
│   ├── Skills/
│   │   ├── SkillsView.swift           # Browse available skills
│   │   └── SkillDetailView.swift      # Skill info + trigger
│   │
│   └── Settings/
│       ├── SettingsView.swift          # App preferences
│       ├── ModelPickerView.swift       # Model selection
│       └── ServerConfigView.swift      # API URL configuration
│
├── Services/
│   ├── APIClient.swift                 # HTTP client (URLSession)
│   ├── SSEClient.swift                 # Server-Sent Events parser
│   ├── AuthManager.swift               # Token lifecycle management
│   ├── ConversationService.swift       # Conversation CRUD
│   ├── ArtifactService.swift           # Artifact listing + download
│   └── PushNotificationService.swift   # APNS registration
│
├── Models/
│   ├── Conversation.swift              # SwiftData model
│   ├── Message.swift                   # SwiftData model
│   ├── Artifact.swift                  # Artifact metadata
│   ├── Skill.swift                     # Skill info
│   ├── AgentInfo.swift                 # Server capabilities
│   └── SSEEvent.swift                  # Streaming event types
│
├── Shared/
│   ├── Theme.swift                     # Colors, typography
│   ├── Extensions/
│   │   ├── String+Markdown.swift       # Markdown rendering
│   │   ├── View+Loading.swift          # Loading state modifiers
│   │   └── Date+Formatting.swift       # Relative date strings
│   └── Components/
│       ├── MarkdownText.swift          # Rendered markdown text
│       ├── CodeBlock.swift             # Syntax-highlighted code
│       ├── LoadingDots.swift           # Animated thinking indicator
│       └── EmptyState.swift            # Empty state illustrations
│
└── Resources/
    ├── Assets.xcassets                 # Icons, colors
    └── Info.plist
```

---

## Core Flows

### 1. Authentication

```
App Launch
    │
    ├── Has stored tokens in Keychain?
    │   ├── Yes → Validate access token
    │   │         ├── Valid → Load conversations
    │   │         └── Expired → Refresh token
    │   │                       ├── Success → Load conversations
    │   │                       └── Fail → Show auth screen
    │   └── No → Show auth screen
    │
    Auth Screen
    │   User enters: API URL + API Key
    │   │
    │   POST /api/v1/auth/register
    │   Body: { device_id, device_name, platform, api_key }
    │   │
    │   ├── 200 → Store tokens in Keychain → Load conversations
    │   └── 401 → Show error
```

### 2. Sending a Message (Streaming)

```
User taps Send
    │
    ChatViewModel.send(message)
    │
    ├── Optimistically add user bubble to UI
    ├── Add empty assistant bubble with streaming indicator
    │
    POST /api/v1/conversations/{id}/messages/stream
    Headers: Authorization: Bearer {access_token}
    Body: { message, images?, command?, model? }
    │
    SSE Stream begins:
    │
    ├── {"type": "status", "status": "processing"}
    │   └── Show "Thinking..." indicator
    │
    ├── {"type": "tool", "name": "Grep", "status": "started"}
    │   └── Show tool badge: "⏳ Grep"
    │
    ├── {"type": "text", "text": "Here is..."}
    │   └── Append to assistant bubble, render markdown incrementally
    │
    ├── {"type": "artifact", "path": "report.html", "action": "updated"}
    │   └── Show inline ArtifactCard with "View in Canvas" button
    │
    ├── {"type": "done", "session_id": "...", "tools_used": [...]}
    │   └── Finalize message, update conversation metadata
    │
    └── {"type": "error", "error": "..."}
        └── Show error banner
```

### 3. Canvas / Artifact Preview

```
User taps ArtifactCard or "View in Canvas"
    │
    CanvasView opens (sheet or split view on iPad)
    │
    CanvasViewModel.load(path)
    │
    ├── Classify file type by extension
    │
    ├── HTML → HTMLPreview (WKWebView)
    │   └── URL: {api_url}/artifacts/{path}
    │
    ├── PDF → PDFPreview (PDFKit)
    │   └── Download to temp file, load PDFDocument
    │
    ├── Markdown → MarkdownPreview
    │   └── GET /api/v1/artifacts?path={path}
    │       Parse and render with AttributedString
    │
    ├── Image → ImagePreview
    │   └── AsyncImage with zoom gesture
    │
    └── Code → CodePreview
        └── Syntax highlighted with monospace font
```

---

## Key Implementation Details

### APIClient.swift

```swift
final class APIClient: ObservableObject {
    let baseURL: URL
    private let session: URLSession
    private let authManager: AuthManager

    init(baseURL: URL, authManager: AuthManager) {
        self.baseURL = baseURL
        self.authManager = authManager
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 300
        self.session = URLSession(configuration: config)
    }

    func request<T: Decodable>(
        _ method: String,
        path: String,
        body: Encodable? = nil
    ) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        // Add auth
        if let token = await authManager.accessToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        if let body {
            request.httpBody = try JSONEncoder().encode(body)
        }

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        if http.statusCode == 401 {
            // Try refresh
            try await authManager.refreshTokens()
            // Retry once
            if let token = await authManager.accessToken {
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            }
            let (retryData, retryResponse) = try await session.data(for: request)
            guard let retryHttp = retryResponse as? HTTPURLResponse,
                  (200...299).contains(retryHttp.statusCode) else {
                throw APIError.unauthorized
            }
            return try JSONDecoder().decode(T.self, from: retryData)
        }

        guard (200...299).contains(http.statusCode) else {
            throw APIError.httpError(http.statusCode, data)
        }

        return try JSONDecoder().decode(T.self, from: data)
    }
}
```

### SSEClient.swift

```swift
final class SSEClient {
    private var task: URLSessionDataTask?
    private let url: URL
    private let authManager: AuthManager

    func stream(
        path: String,
        body: Encodable,
        onEvent: @escaping (SSEEvent) -> Void
    ) async throws {
        var request = URLRequest(url: url.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

        if let token = await authManager.accessToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        request.httpBody = try JSONEncoder().encode(body)

        // Use URLSession bytes for streaming
        let (bytes, response) = try await URLSession.shared.bytes(for: request)

        guard let http = response as? HTTPURLResponse,
              (200...299).contains(http.statusCode) else {
            throw APIError.httpError(
                (response as? HTTPURLResponse)?.statusCode ?? 0, Data()
            )
        }

        var buffer = ""
        for try await line in bytes.lines {
            if line.hasPrefix("data: ") {
                let json = String(line.dropFirst(6))
                if let data = json.data(using: .utf8),
                   let event = try? JSONDecoder().decode(SSEEvent.self, from: data) {
                    await MainActor.run { onEvent(event) }
                }
            }
        }
    }
}
```

### SSEEvent.swift

```swift
enum SSEEvent: Decodable {
    case status(String)
    case text(String)
    case tool(name: String, status: String, filePath: String?)
    case artifact(path: String, action: String)
    case done(sessionId: String, toolsUsed: [String], usage: [String: Int])
    case error(String)

    enum CodingKeys: String, CodingKey {
        case type, status, text, name, path, action
        case sessionId = "session_id"
        case toolsUsed = "tools_used"
        case usage, error
        case filePath = "file_path"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)

        switch type {
        case "status":
            self = .status(try container.decode(String.self, forKey: .status))
        case "text":
            self = .text(try container.decode(String.self, forKey: .text))
        case "tool":
            self = .tool(
                name: try container.decode(String.self, forKey: .name),
                status: try container.decode(String.self, forKey: .status),
                filePath: try? container.decode(String.self, forKey: .filePath)
            )
        case "artifact":
            self = .artifact(
                path: try container.decode(String.self, forKey: .path),
                action: try container.decode(String.self, forKey: .action)
            )
        case "done":
            self = .done(
                sessionId: try container.decode(String.self, forKey: .sessionId),
                toolsUsed: (try? container.decode([String].self, forKey: .toolsUsed)) ?? [],
                usage: (try? container.decode([String: Int].self, forKey: .usage)) ?? [:]
            )
        case "error":
            self = .error(try container.decode(String.self, forKey: .error))
        default:
            self = .status("unknown")
        }
    }
}
```

---

## Gateway API Reference (iOS Client Perspective)

All endpoints are prefixed with `/api/v1/`.

### Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/register` | API Key (in body) | Register device, get tokens |
| `POST` | `/auth/refresh` | None (refresh token in body) | Refresh access token |
| `POST` | `/auth/apns` | Bearer token | Register APNS push token |

### Conversations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/conversations` | Bearer | List conversations (paginated) |
| `POST` | `/conversations` | Bearer | Create new conversation |
| `GET` | `/conversations/{id}` | Bearer | Get conversation + messages |
| `PATCH` | `/conversations/{id}` | Bearer | Update title/pin/archive |
| `DELETE` | `/conversations/{id}` | Bearer | Delete conversation |
| `POST` | `/conversations/{id}/messages` | Bearer | Send message (non-streaming) |
| `POST` | `/conversations/{id}/messages/stream` | Bearer | Send message (SSE streaming) |
| `POST` | `/conversations/{id}/interrupt` | Bearer | Interrupt active stream |

### Artifacts

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/artifacts` | Bearer | List artifacts with type metadata |

### Agent

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/agent/info` | Bearer | Capabilities, skills, models |
| `GET` | `/agent/skills` | Bearer | List available skills |
| `GET` | `/agent/commands` | Bearer | List slash commands |

### Settings

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/settings` | Bearer | Get device preferences |
| `PUT` | `/settings` | Bearer | Update preferences |

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Gateway health check |

---

## UI Design Principles

### Layout

- **iPhone**: Single-column navigation. Conversation list → Chat view. Canvas opens as a sheet.
- **iPad**: Split view. Sidebar (conversations) + Chat + Canvas (trailing panel).

### Theme

```swift
extension Color {
    static let accent = Color(red: 0.85, green: 0.47, blue: 0.34) // #d97757
    static let bgPrimary = Color(red: 0.06, green: 0.06, blue: 0.07)
    static let bgSecondary = Color(red: 0.09, green: 0.09, blue: 0.10)
    static let bgTertiary = Color(red: 0.13, green: 0.13, blue: 0.14)
    static let textPrimary = Color(red: 0.93, green: 0.93, blue: 0.93)
    static let textSecondary = Color(red: 0.65, green: 0.65, blue: 0.65)
    static let textMuted = Color(red: 0.45, green: 0.45, blue: 0.45)
}
```

### Message Rendering

- Markdown rendered via `AttributedString` (iOS 15+) or a lightweight markdown parser
- Code blocks use monospace font with syntax highlighting
- Tool usage shown as compact badges above the message
- Artifact references rendered as tappable cards with file type icons
- Images rendered inline with tap-to-zoom

### Interactions

- Pull-to-refresh on conversation list
- Swipe left to delete, swipe right to pin/archive
- Long press on message to copy, share, or re-send
- Haptic feedback on send, tool completion, and errors
- Keyboard dismissal on scroll

---

## Local Persistence (SwiftData)

```swift
@Model
class Conversation {
    @Attribute(.unique) var id: String
    var title: String
    var lastMessagePreview: String
    var lastActive: Date
    var created: Date
    var messageCount: Int
    var pinned: Bool
    var archived: Bool
    var model: String?

    @Relationship(deleteRule: .cascade)
    var messages: [Message]
}

@Model
class Message {
    @Attribute(.unique) var id: String
    var role: String  // "user" or "assistant"
    var content: String
    var timestamp: Date
    var toolsUsed: [String]
    var artifactPaths: [String]

    var conversation: Conversation?
}
```

**Strategy**: Server is source of truth. Local SwiftData cache enables:
- Instant UI rendering while fetching from server
- Offline viewing of cached conversations
- Background sync when connectivity resumes

---

## Security

- **Tokens**: Stored in iOS Keychain (not UserDefaults)
- **API Key**: Only used once during device registration, then discarded
- **Network**: All traffic over HTTPS (ATS enforced)
- **Biometric**: Optional Face ID / Touch ID gate before app launch
- **Data**: SwiftData encrypted at rest by default on iOS

---

## Push Notifications (Future)

When the agent completes a long-running task asynchronously:

1. Backend stores APNS token via `POST /api/v1/auth/apns`
2. After agent completes work, backend sends push via APNS
3. iOS shows notification with conversation preview
4. Tap opens directly to the relevant conversation

**Requires**: Apple Developer account, APNS certificates configured on server.

---

## Dependencies

| Package | Purpose | Source |
|---------|---------|--------|
| None (pure SwiftUI) | UI framework | Apple |
| SwiftData | Local persistence | Apple |
| PDFKit | PDF rendering | Apple |
| WebKit | HTML artifact preview | Apple |

**Design goal**: Zero third-party dependencies. Use only Apple frameworks.
