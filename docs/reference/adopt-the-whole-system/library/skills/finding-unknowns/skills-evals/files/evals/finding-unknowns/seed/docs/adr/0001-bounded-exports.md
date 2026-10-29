# 0001: Bound export memory and response work

Status: Accepted

The service may export collections larger than process memory. Export work
must produce bounded chunks, with backpressure from the consumer. Building
the complete dataset in memory before sending a response is excluded. A
synchronous request that waits to dump the entire dataset before returning
is excluded as well. A streaming response or an asynchronous job that emits
bounded chunks can satisfy this decision.
