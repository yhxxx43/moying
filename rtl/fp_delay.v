`timescale 1ns/1ps
// Data/metadata delays have exactly the same global CE as every FP operation.
module fp_delay #(parameter WIDTH=1, parameter DEPTH=1)(
    input wire clk, rstn, ce,
    input wire [WIDTH-1:0] in_data,
    output wire [WIDTH-1:0] out_data
);
    reg [WIDTH-1:0] stage [0:DEPTH-1];
    integer i;
    always @(posedge clk) begin
        if(!rstn) begin
            for(i=0;i<DEPTH;i=i+1) stage[i]<=0;
        end else if(ce) begin
            stage[0]<=in_data;
            for(i=1;i<DEPTH;i=i+1) stage[i]<=stage[i-1];
        end
    end
    assign out_data=stage[DEPTH-1];
endmodule
