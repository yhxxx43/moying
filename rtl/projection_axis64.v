`timescale 1ns/1ps
// Strict 64-bit pixel transport. Illegal beats latch a fault, never enter core.
module projection_axis64(
    (* X_INTERFACE_PARAMETER="ASSOCIATED_BUSIF s_axis:m_axis, ASSOCIATED_RESET rstn" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 clk CLK" *) input wire clk,
    (* X_INTERFACE_PARAMETER="POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 rstn RST" *) input wire rstn,
    input wire [31:0] ctrl, output wire [31:0] status,
    input wire [63:0] s_axis_tdata, input wire [7:0] s_axis_tkeep,
    input wire s_axis_tvalid, output wire s_axis_tready, input wire s_axis_tlast,
    output wire [31:0] m_axis_tdata, output wire [3:0] m_axis_tkeep,
    output wire m_axis_tvalid, input wire m_axis_tready, output wire m_axis_tlast
);
    wire core_rstn=rstn && !ctrl[0];
    reg bad_keep=0, bad_reserved=0;
    wire fault=bad_keep || bad_reserved;
    wire invalid_keep=(s_axis_tkeep!=8'hff);
    wire invalid_reserved=(s_axis_tdata[63:48]!=0);
    wire core_ready;
    assign s_axis_tready=core_ready && !fault;
    always @(posedge clk) begin
        if(!core_rstn) begin bad_keep<=0; bad_reserved<=0; end
        else if(s_axis_tvalid && s_axis_tready) begin
            bad_keep<=bad_keep || invalid_keep;
            bad_reserved<=bad_reserved || invalid_reserved;
        end
    end
    assign status={24'h50524a,5'b0,bad_reserved,bad_keep,(core_rstn && !fault)};
    projection_core core(.clk(clk),.rstn(core_rstn),.data(s_axis_tdata[47:0]),
        .valid(s_axis_tvalid && !fault && !invalid_keep && !invalid_reserved),
        .ready(core_ready),.last(s_axis_tlast),.out_data(m_axis_tdata),
        .out_valid(m_axis_tvalid),.out_ready(m_axis_tready),.out_last(m_axis_tlast));
    assign m_axis_tkeep=4'hf;
endmodule
