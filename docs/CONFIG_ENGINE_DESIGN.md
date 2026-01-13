# Switchcraft Config Engine Design

## Problem Statement

Current architecture requires AI API round-trips for every decision:
```
AI → MCP → Switch → MCP → AI → MCP → Switch → ... (repeat N times)
     ~~~2-5s~~~           ~~~2-5s~~~
```

A simple VLAN change with 4 ports can take 30+ seconds due to API latency, not switch latency.

## Solution: Two-Layer Architecture

### Layer 1: AI Intent Layer
- AI sends **desired state** (declarative), not commands (imperative)
- Single MCP tool call: `apply_config` with full desired state
- AI only re-engaged if auto-recovery fails

### Layer 2: Config Engine (new)
- Validates desired state before touching any device
- Calculates diff against current state
- Generates optimized command batches
- Executes with automatic error recovery
- Returns success/failure with details

```
┌──────────────────────────────────────────────────────────────────┐
│                        AI LAYER                                  │
│  "Ensure VLAN 100 exists with ports 1/1/1-4, 1/2/1-4 untagged"  │
└────────────────────────────┬─────────────────────────────────────┘
                             │ apply_config(desired_state)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                     CONFIG ENGINE                                │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │    PARSE     │───▶│   VALIDATE   │───▶│     DIFF     │       │
│  │ Desired State│    │  Pre-flight  │    │ vs Current   │       │
│  └──────────────┘    └──────────────┘    └──────┬───────┘       │
│                                                  │               │
│                                                  ▼               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │    REPORT    │◀───│  AUTO-FIX    │◀───│   EXECUTE    │       │
│  │   Results    │    │ Known Errors │    │    Batch     │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                   │                                    │
│         │                   │ Only if auto-fix fails             │
│         ▼                   ▼                                    │
│  ┌─────────────────────────────────────┐                        │
│  │  Return to AI with error context    │                        │
│  │  for intelligent troubleshooting    │                        │
│  └─────────────────────────────────────┘                        │
└──────────────────────────────────────────────────────────────────┘
```

---

## Desired State Schema

### Network Config Format (YAML/JSON)

```yaml
# Desired state for a device
device: brocade-core
version: 1
checksum: sha256:abc123...  # Integrity check

vlans:
  100:
    name: "Production"
    untagged_ports: ["1/1/1", "1/1/2", "1/1/3", "1/1/4"]
    tagged_ports: []

  200:
    name: "Management"
    untagged_ports: ["1/1/5", "1/1/6"]
    tagged_ports: ["1/2/1"]
    ip_interface:
      address: "192.168.200.1"
      mask: "255.255.255.0"

  254:
    name: "Infrastructure"
    untagged_ports: ["1/2/2", "1/2/3", "1/2/4"]
    tagged_ports: []

ports:
  "1/1/1":
    enabled: true
    description: "Server-01"
    speed: auto

  "1/2/1":
    enabled: true
    description: "Uplink-Core"
    speed: 10G

# Global settings
settings:
  stp_enabled: true
  default_vlan: 1
```

### Partial Updates (Patches)

For incremental changes without specifying full state:

```yaml
device: brocade-core
version: 1
mode: patch  # Only modify specified items

vlans:
  100:
    action: ensure  # create if missing, update if different
    name: "Production"
    untagged_ports: ["1/1/1", "1/1/2", "1/1/3", "1/1/4", "1/2/1", "1/2/2"]

  999:
    action: absent  # delete if exists
```

---

## Config Engine Components

### 1. Parser (`config_engine/parser.py`)

```python
@dataclass
class DesiredState:
    device_id: str
    version: int
    checksum: Optional[str]
    mode: Literal["full", "patch"] = "patch"
    vlans: dict[int, VLANDesiredState]
    ports: dict[str, PortDesiredState]
    settings: dict[str, Any]

@dataclass
class VLANDesiredState:
    action: Literal["ensure", "absent"] = "ensure"
    name: Optional[str] = None
    untagged_ports: list[str] = field(default_factory=list)
    tagged_ports: list[str] = field(default_factory=list)
    ip_interface: Optional[IPInterface] = None
```

### 2. Validator (`config_engine/validator.py`)

Pre-flight checks before any switch communication:

```python
class ConfigValidator:
    """Validate desired state for logical errors."""

    def validate(self, desired: DesiredState) -> ValidationResult:
        errors = []
        warnings = []

        # Check 1: VLAN ID ranges
        for vlan_id in desired.vlans:
            if not 1 <= vlan_id <= 4094:
                errors.append(f"Invalid VLAN ID: {vlan_id}")
            if vlan_id == 1 and desired.vlans[vlan_id].action == "absent":
                errors.append("Cannot delete VLAN 1 (protected)")

        # Check 2: Port conflicts (same port in multiple VLANs untagged)
        untagged_assignments = {}
        for vlan_id, vlan in desired.vlans.items():
            for port in vlan.untagged_ports:
                if port in untagged_assignments:
                    errors.append(
                        f"Port {port} assigned untagged to both "
                        f"VLAN {untagged_assignments[port]} and {vlan_id}"
                    )
                untagged_assignments[port] = vlan_id

        # Check 3: Port name validation
        for port in desired.ports:
            if not self._valid_port_name(port, desired.device_id):
                errors.append(f"Invalid port name: {port}")

        # Check 4: Checksum verification (if provided)
        if desired.checksum:
            if not self._verify_checksum(desired):
                errors.append("Config checksum mismatch - possible corruption")

        # Warning: Large change set
        total_changes = len(desired.vlans) + len(desired.ports)
        if total_changes > 20:
            warnings.append(f"Large change set ({total_changes} items) - consider staging")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )
```

### 3. Diff Engine (`config_engine/diff.py`)

Calculate minimal changes needed:

```python
class ConfigDiff:
    """Calculate difference between desired and current state."""

    async def calculate(
        self,
        device: NetworkDevice,
        desired: DesiredState
    ) -> DiffResult:
        """
        Returns:
            DiffResult with:
            - vlans_to_create: list[VLANConfig]
            - vlans_to_modify: list[VLANModification]
            - vlans_to_delete: list[int]
            - ports_to_configure: list[PortModification]
            - no_change: bool
        """
        # Fetch current state (single batch query)
        current_vlans = await device.get_vlans()
        current_ports = await device.get_ports()

        diff = DiffResult()

        for vlan_id, desired_vlan in desired.vlans.items():
            current = self._find_vlan(current_vlans, vlan_id)

            if desired_vlan.action == "absent":
                if current:
                    diff.vlans_to_delete.append(vlan_id)
            else:  # ensure
                if not current:
                    diff.vlans_to_create.append(desired_vlan.to_config())
                elif self._vlan_differs(current, desired_vlan):
                    diff.vlans_to_modify.append(
                        VLANModification(vlan_id, current, desired_vlan)
                    )

        return diff
```

### 4. Command Generator (`config_engine/generator.py`)

Generate optimized command batches:

```python
class CommandGenerator:
    """Generate device-specific command batches from diff."""

    def generate(
        self,
        device_type: str,
        diff: DiffResult
    ) -> CommandPlan:
        """
        Returns CommandPlan with:
        - pre_commands: Setup commands (e.g., disable dual-mode)
        - main_commands: Core changes
        - post_commands: Cleanup/verification
        - rollback_commands: Undo if failure
        """
        if device_type == "brocade":
            return self._generate_brocade(diff)
        elif device_type == "openwrt":
            return self._generate_openwrt(diff)
        # ... other device types

    def _generate_brocade(self, diff: DiffResult) -> CommandPlan:
        plan = CommandPlan()

        # Pre-commands: Handle known blockers
        for mod in diff.vlans_to_modify:
            # Check if any ports need dual-mode disabled
            ports_changing_from_tagged = self._get_tagged_to_untagged(mod)
            for port in ports_changing_from_tagged:
                plan.pre_commands.append(f"interface ethe {port}")
                plan.pre_commands.append("no dual-mode")
                plan.pre_commands.append("exit")

        # Main commands: VLAN changes (grouped by module!)
        for vlan in diff.vlans_to_create:
            plan.main_commands.append(f"vlan {vlan.id} name {vlan.name} by port")
            for port_spec in self._group_ports_by_module(vlan.untagged_ports):
                plan.main_commands.append(f"untagged ethe {port_spec}")
            for port_spec in self._group_ports_by_module(vlan.tagged_ports):
                plan.main_commands.append(f"tagged ethe {port_spec}")
            plan.main_commands.append("exit")

        # Generate rollback commands (reverse order)
        plan.rollback_commands = self._generate_rollback(diff)

        return plan
```

### 5. Executor with Auto-Recovery (`config_engine/executor.py`)

```python
class ConfigExecutor:
    """Execute command plan with automatic error recovery."""

    # Known error patterns and their fixes
    RECOVERY_PATTERNS = {
        "Please disable dual mode": RecoveryAction.DISABLE_DUAL_MODE,
        "already a member": RecoveryAction.IGNORE,  # Not an error
        "Port is in spanning-tree": RecoveryAction.DISABLE_STP_PORT,
    }

    async def execute(
        self,
        device: NetworkDevice,
        plan: CommandPlan,
        options: ExecuteOptions
    ) -> ExecuteResult:
        """
        Execute plan with recovery attempts.

        Options:
            dry_run: bool - Preview only, don't execute
            max_recovery_attempts: int - How many auto-fix attempts
            stop_on_error: bool - Abort on first unrecoverable error
            audit_context: str - Description for audit log
        """
        result = ExecuteResult()

        # DRY RUN MODE (enterprise feature)
        if options.dry_run:
            return self._dry_run(device, plan)

        # AUDIT LOG (enterprise feature)
        audit_entry = AuditEntry(
            timestamp=datetime.utcnow(),
            device_id=device.device_id,
            operation="apply_config",
            context=options.audit_context,
            user=options.user or "system",
        )

        try:
            async with device:
                # Execute pre-commands
                for cmd in plan.pre_commands:
                    await self._execute_with_recovery(device, cmd, result)

                # Execute main commands as batch
                success, output = await device.execute_config_batch(
                    plan.main_commands,
                    stop_on_error=True
                )

                if not success:
                    # Try auto-recovery
                    recovered = await self._attempt_recovery(
                        device, output, plan, result
                    )
                    if not recovered:
                        result.success = False
                        result.requires_ai_intervention = True
                        result.error_context = output
                        return result

                # Execute post-commands (verification)
                await self._verify_state(device, plan, result)

                # Save config
                await device.execute("write memory")

                result.success = True

        except Exception as e:
            result.success = False
            result.error = str(e)
            result.requires_ai_intervention = True

            # Attempt rollback if we have commands
            if plan.rollback_commands and options.rollback_on_error:
                await self._execute_rollback(device, plan, result)

        finally:
            # Always log to audit
            audit_entry.success = result.success
            audit_entry.changes = result.changes_made
            audit_entry.error = result.error
            await self._audit_log.write(audit_entry)

        return result

    async def _attempt_recovery(
        self,
        device: NetworkDevice,
        error_output: str,
        plan: CommandPlan,
        result: ExecuteResult
    ) -> bool:
        """
        Attempt automatic recovery from known errors.
        Returns True if recovered successfully.
        """
        for pattern, action in self.RECOVERY_PATTERNS.items():
            if pattern.lower() in error_output.lower():
                result.recovery_attempts.append(f"Matched: {pattern}")

                if action == RecoveryAction.IGNORE:
                    return True  # Not a real error

                if action == RecoveryAction.DISABLE_DUAL_MODE:
                    # Extract port from error, disable dual-mode, retry
                    port = self._extract_port_from_error(error_output)
                    if port:
                        await device.execute_config_batch([
                            f"interface ethe {port}",
                            "no dual-mode",
                            "exit"
                        ])
                        # Retry the failed command
                        return await self._retry_failed_commands(device, plan)

        return False  # Could not recover
```

### 6. MCP Tool Integration (`server.py`)

New tool that uses the engine:

```python
@tool("apply_config")
async def handle_apply_config(
    inv: DeviceInventory,
    config: dict,           # The desired state (YAML/JSON parsed)
    dry_run: bool = False,
    audit_context: str = ""
) -> list[TextContent]:
    """
    Apply a desired configuration state to a device.

    This is the primary tool for making changes. It:
    1. Validates the config for errors
    2. Calculates diff against current state
    3. Generates optimized command batches
    4. Executes with automatic error recovery
    5. Returns detailed results

    Use dry_run=True to preview changes without applying.
    """
    engine = ConfigEngine(inv)

    # Parse desired state
    desired = engine.parse(config)

    # Validate
    validation = engine.validate(desired)
    if not validation.valid:
        return error_response(
            "Config validation failed",
            validation.errors
        )

    # Calculate diff
    diff = await engine.diff(desired)
    if diff.no_change:
        return success_response("No changes needed - state already matches")

    # Generate commands
    plan = engine.generate_commands(desired.device_id, diff)

    # Execute (or dry-run)
    result = await engine.execute(
        desired.device_id,
        plan,
        ExecuteOptions(
            dry_run=dry_run,
            audit_context=audit_context
        )
    )

    # Format response
    if result.success:
        return success_response(
            f"Applied {len(result.changes_made)} changes",
            changes=result.changes_made,
            warnings=validation.warnings
        )
    elif result.requires_ai_intervention:
        return error_response(
            "Auto-recovery failed - AI assistance needed",
            error=result.error,
            context=result.error_context,
            attempted_fixes=result.recovery_attempts
        )
    else:
        return error_response("Execution failed", error=result.error)
```

---

## Enterprise Features Integration

### 1. Dry-Run Mode
Already in executor - preview all changes without applying.

### 2. Audit Logging
Every `apply_config` call logged with:
- Timestamp
- User/source
- Device
- Desired state checksum
- Changes made
- Success/failure
- Recovery attempts

### 3. Rollback Support
CommandPlan includes rollback commands. On failure:
- Automatic rollback if `rollback_on_error=True`
- Manual rollback via `rollback_config(audit_id)`

### 4. Config Checksums
- Desired state can include checksum for integrity
- Prevents corrupted configs from being applied
- Useful for config-as-code pipelines

### 5. Change Staging
For large changes:
```python
# Stage changes without applying
stage_id = await engine.stage_config(desired_state)

# Review staged changes
staged = await engine.get_staged(stage_id)

# Apply staged changes
result = await engine.apply_staged(stage_id)

# Or discard
await engine.discard_staged(stage_id)
```

---

## File Structure

```
src/mcp_network_switch/
├── config_engine/
│   ├── __init__.py
│   ├── engine.py          # Main ConfigEngine class
│   ├── parser.py          # Desired state parsing
│   ├── validator.py       # Pre-flight validation
│   ├── diff.py            # State diffing
│   ├── generator.py       # Command generation
│   ├── executor.py        # Execution with recovery
│   ├── recovery.py        # Auto-recovery patterns
│   └── schema.py          # Dataclasses/types
├── devices/
│   └── ...
└── server.py              # Add apply_config tool
```

---

## Example Usage Flow

### Before (many API round-trips):
```
AI: get_vlans                    → 2s API + 13s switch
AI: "I see VLAN 254 exists"      → 2s API
AI: create_vlan (ports 1/1/1-4)  → 2s API + 14s switch
AI: "Error: dual-mode"           → 2s API
AI: execute_command(no dual-mode)→ 2s API + 13s switch
AI: create_vlan (retry)          → 2s API + 14s switch
AI: get_vlans (verify)           → 2s API + 13s switch
AI: save_config                  → 2s API + 13s switch
─────────────────────────────────
Total: ~16s API + ~80s switch = ~96 seconds
```

### After (single API call):
```
AI: apply_config({              → 2s API
      vlans: {
        254: {
          untagged_ports: ["1/1/1-4", "1/2/1-4"]
        }
      }
    })
                                  ↓
Engine: validate → diff → generate → execute (with auto-recovery) → verify
                                  ↓
AI: "Success: 3 changes applied" ← response
─────────────────────────────────
Total: ~2s API + ~15s switch = ~17 seconds (5.6x faster)
```

---

## Implementation Phases

### Phase 1: Core Engine (MVP)
- [ ] DesiredState schema and parser
- [ ] Basic validator (VLAN IDs, port conflicts)
- [ ] Diff engine
- [ ] Brocade command generator
- [ ] Basic executor (no recovery)
- [ ] `apply_config` MCP tool

### Phase 2: Auto-Recovery
- [ ] Recovery pattern registry
- [ ] Dual-mode auto-fix
- [ ] "Already a member" handling
- [ ] Retry logic

### Phase 3: Enterprise Features
- [ ] Full audit logging integration
- [ ] Rollback commands + manual rollback tool
- [ ] Config checksums
- [ ] Change staging

### Phase 4: Multi-Device
- [ ] OpenWrt command generator
- [ ] Zyxel command generator (when available)
- [ ] Cross-device transactions

---

## Open Questions

1. **Config format**: YAML vs JSON vs custom DSL?
2. **Partial vs Full state**: Always require full state or support patches?
3. **Conflict resolution**: What if current state was manually changed?
4. **Multi-device atomicity**: How to handle cross-device changes?
