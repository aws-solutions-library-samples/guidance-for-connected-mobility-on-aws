# Autonomic feed interface

## Overview

The Autonomic feed interface provide scalable, real-time event stream topologies 
with granular filtering of events for different purposes and consumers. The feed 
interface is intended to support low complexity filtering of high volume streams 
to meet the needs of telematics service providers. We expect other consumers of
this interface to emerge over time. 

## Concepts

The primary abstractions in the feed interface are flows, that represent event 
streams, and taps, that represent filtered connections between flows. Flows are
further divided into shards that present a time-ordered sequence of events based
on a per-event key. Events with the same key will be delivered to the same shard.



## Administrative interface


## Consumer interface


## Telematics service provider example

    [OEM raw vehicle event flow]
           |_________________[TSP vehicle tap]
                                         |__________[TSP vehicle event flow]
 