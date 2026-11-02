`timescale 1ns/1ps
// Domain: six uint8 channels. The 18-bit integer front end is EXACT in FP32
// (all sums <=195075 <2**24). Division and residual use distinct rounded ops.
module projection_core(
    input wire clk,rstn, input wire [47:0] data,
    input wire valid, output wire ready, input wire last,
    output wire [31:0] out_data, output wire out_valid,
    input wire out_ready, output wire out_last
);
    `include "fp_latency.vh"
    localparam TAIL=LAT_MUL+LAT_SUB+LAT_MUL+LAT_ADD+LAT_ADD+LAT_DIV;
    wire ce=!out_valid || out_ready;
    assign ready=rstn && ce;
    function [31:0] uint18_fp;
        input [17:0] value;
        integer i;
        reg [7:0] exponent;
        reg [40:0] scaled;
        begin
            uint18_fp=0;
            exponent=0; scaled=0;
            for(i=0;i<18;i=i+1) if(value[i]) begin
                exponent=127+i;
                scaled={23'b0,value} << (23-i);
                uint18_fp={1'b0,exponent,scaled[22:0]};
            end
        end
    endfunction
    function [7:0] abs8;
        input [7:0] a,b;
        begin abs8=a>=b ? a-b : b-a; end
    endfunction
    wire [7:0] cb=data[7:0],cg=data[15:8],cr=data[23:16];
    wire [7:0] bb=data[31:24],bg=data[39:32],br=data[47:40];
    wire [15:0] pbb=bb*bb,pbg=bg*bg,pbr=br*br;
    wire [15:0] pcb=cb*bb,pcg=cg*bg,pcr=cr*br;
    wire [17:0] power={2'b0,pbb}+{2'b0,pbg}+{2'b0,pbr};
    wire [17:0] dot={2'b0,pcb}+{2'b0,pcg}+{2'b0,pcr};
    wire delta_gt25=abs8(cb,bb)>25 || abs8(cg,bg)>25 || abs8(cr,br)>25;
    reg [17:0] power_s=0,dot_s=0;
    reg [50:0] meta_s=0;
    reg valid_s=0;
    always @(posedge clk) begin
        if(!rstn) begin valid_s<=0; power_s<=0; dot_s<=0; meta_s<=0; end
        else if(ce) begin
            valid_s<=valid;
            power_s<=power; dot_s<=dot;
            meta_s<={last,delta_gt25,(power>=4800),data};
        end
    end
    wire [31:0] ratio;
    wire ratio_valid;
    fp_div ratio_op(.aclk(clk),.aclken(ce),.aresetn(rstn),
        .s_axis_a_tvalid(valid_s),.s_axis_a_tdata(uint18_fp(dot_s)),
        .s_axis_b_tvalid(valid_s),.s_axis_b_tdata(uint18_fp(power_s==0 ? 18'd1 : power_s)),
        .m_axis_result_tvalid(ratio_valid),.m_axis_result_tdata(ratio));
    wire [50:0] ratio_meta,multiply_meta;
    fp_delay #(.WIDTH(51),.DEPTH(LAT_DIV)) ratio_side(clk,rstn,ce,meta_s,ratio_meta);
    fp_delay #(.WIDTH(51),.DEPTH(LAT_MUL)) multiply_side(clk,rstn,ce,ratio_meta,multiply_meta);
    wire [31:0] projected[0:2],difference[0:2],squared[0:2];
    wire [2:0] projected_valid,difference_valid,squared_valid;
    genvar ch;
    generate for(ch=0;ch<3;ch=ch+1) begin: channels
        fp_mul project_op(.aclk(clk),.aclken(ce),.aresetn(rstn),
            .s_axis_a_tvalid(ratio_valid),.s_axis_a_tdata(ratio),
            .s_axis_b_tvalid(ratio_valid),.s_axis_b_tdata(uint18_fp({10'b0,ratio_meta[24+ch*8+:8]})),
            .m_axis_result_tvalid(projected_valid[ch]),.m_axis_result_tdata(projected[ch]));
        fp_sub subtract_op(.aclk(clk),.aclken(ce),.aresetn(rstn),
            .s_axis_a_tvalid(projected_valid[ch]),.s_axis_a_tdata(uint18_fp({10'b0,multiply_meta[ch*8+:8]})),
            .s_axis_b_tvalid(projected_valid[ch]),.s_axis_b_tdata(projected[ch]),
            .m_axis_result_tvalid(difference_valid[ch]),.m_axis_result_tdata(difference[ch]));
        fp_mul square_op(.aclk(clk),.aclken(ce),.aresetn(rstn),
            .s_axis_a_tvalid(difference_valid[ch]),.s_axis_a_tdata(difference[ch]),
            .s_axis_b_tvalid(difference_valid[ch]),.s_axis_b_tdata(difference[ch]),
            .m_axis_result_tvalid(squared_valid[ch]),.m_axis_result_tdata(squared[ch]));
    end endgenerate
    wire [31:0] sum_bg,red_delayed,sum_all,mean;
    wire sum_bg_valid,sum_all_valid,mean_valid;
    fp_add sum_bg_op(.aclk(clk),.aclken(ce),.aresetn(rstn),
        .s_axis_a_tvalid(squared_valid[0]),.s_axis_a_tdata(squared[0]),
        .s_axis_b_tvalid(squared_valid[1]),.s_axis_b_tdata(squared[1]),
        .m_axis_result_tvalid(sum_bg_valid),.m_axis_result_tdata(sum_bg));
    fp_delay #(.WIDTH(32),.DEPTH(LAT_ADD)) red_side(clk,rstn,ce,squared[2],red_delayed);
    fp_add sum_all_op(.aclk(clk),.aclken(ce),.aresetn(rstn),
        .s_axis_a_tvalid(sum_bg_valid),.s_axis_a_tdata(sum_bg),
        .s_axis_b_tvalid(sum_bg_valid),.s_axis_b_tdata(red_delayed),
        .m_axis_result_tvalid(sum_all_valid),.m_axis_result_tdata(sum_all));
    fp_div mean_op(.aclk(clk),.aclken(ce),.aresetn(rstn),
        .s_axis_a_tvalid(sum_all_valid),.s_axis_a_tdata(sum_all),
        .s_axis_b_tvalid(sum_all_valid),.s_axis_b_tdata(32'h40400000),
        .m_axis_result_tvalid(mean_valid),.m_axis_result_tdata(mean));
    wire [82:0] final_side;
    fp_delay #(.WIDTH(83),.DEPTH(TAIL)) final_meta(clk,rstn,ce,{ratio,ratio_meta},final_side);
    // Internal observation points only: never exported as overlay I/O.
    wire [31:0] debug_ratio=final_side[82:51],debug_residual=mean;
    // Nonnegative finite bit patterns sort numerically in this restricted domain.
    wire similar=final_side[48] && debug_ratio>=32'h3f000000 &&
                 debug_ratio<=32'h3f99999a && mean<=32'h43100000;
    assign out_data=(final_side[49] && !similar) ? 32'h000000ff : 32'h00000000;
    assign out_valid=mean_valid && rstn;
    assign out_last=final_side[50];
endmodule
