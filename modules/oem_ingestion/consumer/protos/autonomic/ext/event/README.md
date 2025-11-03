# Event

Events objects are sent to the Autonomic backend as occurances that should not
be aggregated over time as metrics are.  They represent data about a specific
point in time and may describe state changes on the vehicle.  They *may*
contain metric information and information not processed by the Autonomic
platform.

## StateTransitions

The builder `FsmName` should be set to a string of the full class name.

`MyStateMachine.class.getName()`

The `FromState` and `ToState` should be set to the string enum value of that
state.

`MyStateMachine.State.STATE_ONE.toString()` or `MyStateMachine.State.STATE_TWO.toString()`

These states and objects should be imported from the generated objects from
the state machines package.

The `Message` from a StateTransition event should be set by the event emitter
with any important information about the transition.

## Sample Java

```
Instant now = Instant.now();
Event.newBuilder()
    .setId("0f0c492e-6ec9-4ee0-b6e2-b1a60c941932")
    .setCorrelationId(correlationId)
    .setSource("aui:edge:vehicle")
    .setTimestamp(
       Timestamp.newBuilder()
           .setSeconds(now.getEpochSecond())
           .setNanos(now.getNano()).build()
    )
    .setPayload(Any.pack(
        StateTransition.newBuilder()
            .setMessage("Artifact download completed")
            .setFsmName(MyStateMachine.class.getName())
            .setFromState(MyStateMachine.State.STATE_ONE.toString())
            .setToState(MyStateMachine.State.STATE_ONE.toString())
            .build()

        )
    )
    .build();

```
